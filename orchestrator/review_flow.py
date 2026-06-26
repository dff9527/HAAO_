from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from clients.claude_po import AuditResult
from orchestrator.cloud_usage import CloudUsage, apply_usage_to_requirement
from orchestrator.db.sqlite import RequirementRepository, RunEventRepository, SettingsRepository, TicketRepository
from orchestrator.model_instructions import call_auditor, tech_lead_additional_instructions
from orchestrator.models.ticket import Audit, Ticket, TicketStatus
from orchestrator.notifications import NotificationService
from orchestrator.state_machine import InvalidTransitionError, TicketStateService


class Auditor(Protocol):
    def audit(self, ticket: Ticket | dict, diff: str) -> AuditResult:
        ...


@dataclass(frozen=True)
class ReviewResult:
    ticket: Ticket
    verdict: str
    feedback: str


class ReviewService:
    def __init__(
        self,
        repository: TicketRepository,
        state_service: TicketStateService,
        auditor: Auditor,
        requirement_repository: RequirementRepository | None = None,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self.repository = repository
        self.state_service = state_service
        self.auditor = auditor
        self.requirement_repository = requirement_repository
        self.settings_repository = settings_repository

    def review_ticket(self, ticket_id: str) -> ReviewResult:
        ticket = self._require_ticket(ticket_id)
        if TicketStatus(ticket.status) != TicketStatus.REVIEW:
            raise InvalidTransitionError("Only review-state tickets can be audited")

        diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
        try:
            audit_result = call_auditor(
                self.auditor,
                ticket,
                diff,
                additional_instructions=tech_lead_additional_instructions(self.settings_repository),
            )
            self._record_model_call(ticket, diff)
        except Exception as exc:
            self._record_audit_error(ticket, exc)
            raise
        ticket = self._save_audit(ticket, audit_result)
        self._record_cloud_usage(ticket)

        if audit_result.verdict == "approved":
            moved = self.state_service.move(
                ticket.id,
                TicketStatus.AWAITING_ACCEPTANCE,
            ).ticket
            NotificationService(
                self.repository,
                self.settings_repository,
            ).notify_intervention_needed(moved, "acceptance_required")
        else:
            moved = self.state_service.move(ticket.id, TicketStatus.BACKLOG).ticket

        self.repository.append_log(
            moved.id,
            f"Technical audit {audit_result.verdict}: {audit_result.feedback}",
            level="info" if audit_result.verdict == "approved" else "warn",
        )
        return ReviewResult(
            ticket=moved,
            verdict=audit_result.verdict,
            feedback=audit_result.feedback,
        )

    def _save_audit(self, ticket: Ticket, audit_result: AuditResult) -> Ticket:
        ticket_json = ticket.to_dict()
        ticket_json["audit"] = Audit(
            reviewed_by="claude-tech-lead",
            verdict=audit_result.verdict,
            feedback=audit_result.feedback,
        ).model_dump(mode="json")
        return self.repository.save(Ticket.from_dict(ticket_json))

    def _record_cloud_usage(self, ticket: Ticket) -> None:
        usage = getattr(self.auditor, "last_usage", CloudUsage())
        if not isinstance(usage, CloudUsage) or not (usage.input_tokens or usage.output_tokens):
            return

        ticket_metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        requirement_id = ticket_metadata.get("requirement_id")
        if requirement_id and self.requirement_repository is not None:
            requirement = self.requirement_repository.get(requirement_id)
            if requirement is not None:
                self.requirement_repository.save(apply_usage_to_requirement(requirement, usage))

        ticket_json = ticket.to_dict()
        metadata = dict(ticket_json.get("metadata") or {})
        metadata["cloud_input_tokens"] = int(metadata.get("cloud_input_tokens") or 0) + usage.input_tokens
        metadata["cloud_output_tokens"] = int(metadata.get("cloud_output_tokens") or 0) + usage.output_tokens
        metadata["cloud_cost_usd"] = round(float(metadata.get("cloud_cost_usd") or 0) + usage.cost_usd, 4)
        ticket_json["metadata"] = metadata
        self.repository.save(Ticket.from_dict(ticket_json))

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _record_audit_error(self, ticket: Ticket, exc: Exception) -> None:
        message = f"Technical audit failed: {exc}"
        ticket_json = ticket.to_dict()
        ticket_json["audit"] = Audit(
            reviewed_by="claude-tech-lead",
            verdict="pending",
            feedback=message,
        ).model_dump(mode="json")
        metadata = ticket_json.setdefault("metadata", {})
        metadata["technical_audit_error"] = str(exc)
        self.repository.save(Ticket.from_dict(ticket_json))
        self.repository.append_log(ticket.id, message, level="error")
        RunEventRepository(self.repository.connection).append_run_event(
            project_id=_ticket_project_id(ticket),
            requirement_id=_ticket_requirement_id(ticket),
            ticket_id=ticket.id,
            run_id=_ticket_run_id(ticket),
            event_type="error",
            model_id="claude-tech-lead",
            payload={"stage": "technical_audit", "error": str(exc)},
        )

    def _record_model_call(self, ticket: Ticket, diff: str) -> None:
        usage = getattr(self.auditor, "last_usage", CloudUsage())
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
                "stage": "technical_audit",
                "diff_line_count": len(diff.splitlines()),
                "verdict_source": "auditor",
            },
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
