from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from clients.claude_po import AuditResult
from orchestrator.db.sqlite import SettingsRepository, TicketRepository
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
        audit_result = call_auditor(
            self.tech_lead,
            ticket,
            diff,
            additional_instructions=tech_lead_additional_instructions(self.settings_repository),
        )
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
