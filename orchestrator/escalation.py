from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from clients.claude_po import AuditResult
from orchestrator.cloud_usage import CloudUsage
from orchestrator.db.sqlite import RunEventRepository, SettingsRepository, TicketRepository
from orchestrator.model_instructions import call_auditor, tech_lead_additional_instructions
from orchestrator.models.ticket import Audit, Ticket, TicketStatus


class EscalationAuditor(Protocol):
    def audit(self, ticket: Ticket | dict, diff: str) -> AuditResult:
        ...


@dataclass(frozen=True)
class EscalationResult:
    ticket: Ticket
    handled: bool
    escalated_to: str | None = None
    feedback: str = ""


class EscalationService:
    def __init__(
        self,
        repository: TicketRepository,
        tech_lead: EscalationAuditor,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self.repository = repository
        self.tech_lead = tech_lead
        self.settings_repository = settings_repository

    def handle_blocked_ticket(self, ticket_id: str) -> EscalationResult:
        ticket = self._require_ticket(ticket_id)
        if TicketStatus(ticket.status) != TicketStatus.BLOCKED:
            return EscalationResult(ticket=ticket, handled=False)

        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        escalated_to = metadata.get("escalated_to") or ticket.execution.escalate_to
        if escalated_to != "tech_lead":
            self.repository.append_log(
                ticket.id,
                f"Escalation target is {escalated_to}; no automatic Tech Lead call made",
                level="warn",
            )
            return EscalationResult(
                ticket=ticket,
                handled=False,
                escalated_to=escalated_to,
            )

        diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
        try:
            audit_result = call_auditor(
                self.tech_lead,
                ticket,
                diff,
                additional_instructions=tech_lead_additional_instructions(self.settings_repository),
            )
            self._record_model_call(ticket, diff)
        except Exception as exc:
            self._record_error(ticket, exc)
            raise
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["escalation_handled_by"] = "claude-tech-lead"
        metadata["escalation_handled_at"] = datetime.now(UTC).isoformat()
        metadata["escalation_feedback"] = audit_result.feedback
        ticket_json["audit"] = Audit(
            reviewed_by="claude-tech-lead",
            verdict=audit_result.verdict,
            feedback=audit_result.feedback,
        ).model_dump(mode="json")
        updated = self.repository.save(Ticket.from_dict(ticket_json))
        self.repository.append_log(
            ticket.id,
            f"Tech Lead escalation handled: {audit_result.feedback}",
            level="warn",
        )
        RunEventRepository(self.repository.connection).append_run_event(
            project_id=_ticket_project_id(updated),
            requirement_id=_ticket_requirement_id(updated),
            ticket_id=updated.id,
            run_id=_ticket_run_id(updated),
            event_type="escalation",
            model_id="claude-tech-lead",
            payload={
                "reason": metadata.get("escalation_reason", "blocked_ticket"),
                "handled_by": "claude-tech-lead",
                "verdict": audit_result.verdict,
                "escalated_to": escalated_to,
            },
        )
        return EscalationResult(
            ticket=updated,
            handled=True,
            escalated_to=escalated_to,
            feedback=audit_result.feedback,
        )

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _record_model_call(self, ticket: Ticket, diff: str) -> None:
        usage = getattr(self.tech_lead, "last_usage", CloudUsage())
        if not isinstance(usage, CloudUsage):
            usage = CloudUsage()
        RunEventRepository(self.repository.connection).append_run_event(
            project_id=_ticket_project_id(ticket),
            requirement_id=_ticket_requirement_id(ticket),
            ticket_id=ticket.id,
            run_id=_ticket_run_id(ticket),
            event_type="model_call",
            model_id="claude-tech-lead",
            input_tokens=usage.input_tokens or None,
            output_tokens=usage.output_tokens or None,
            cost_usd=usage.cost_usd if usage.total_tokens else None,
            cost_status=usage.cost_status if usage.total_tokens else "unknown",
            payload={
                "stage": "escalation_audit",
                "diff_line_count": len(diff.splitlines()),
            },
        )

    def _record_error(self, ticket: Ticket, exc: Exception) -> None:
        RunEventRepository(self.repository.connection).append_run_event(
            project_id=_ticket_project_id(ticket),
            requirement_id=_ticket_requirement_id(ticket),
            ticket_id=ticket.id,
            run_id=_ticket_run_id(ticket),
            event_type="error",
            model_id="claude-tech-lead",
            payload={"stage": "escalation_audit", "error": str(exc)},
        )


def _ticket_project_id(ticket: Ticket) -> str:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        project_id = metadata.get("project_id")
        if isinstance(project_id, str) and project_id:
            return project_id
    return "default"


def _ticket_requirement_id(ticket: Ticket) -> str | None:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        requirement_id = metadata.get("requirement_id")
        if isinstance(requirement_id, str) and requirement_id:
            return requirement_id
    return None


def _ticket_run_id(ticket: Ticket) -> str | None:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        run_id = metadata.get("last_run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return None
