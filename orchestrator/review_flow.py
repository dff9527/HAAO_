from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from clients.claude_po import AuditResult
from orchestrator.cloud_usage import CloudUsage, apply_usage_to_requirement
from orchestrator.db.sqlite import RequirementRepository, SettingsRepository, TicketRepository
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
