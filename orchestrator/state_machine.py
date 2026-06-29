from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from orchestrator.db.sqlite import ChatRepository, RunEventRepository, TicketRepository
from orchestrator.models.ticket import Ticket, TicketStatus


LEGAL_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.BACKLOG: frozenset({TicketStatus.READY}),
    TicketStatus.READY: frozenset({TicketStatus.IN_PROGRESS}),
    TicketStatus.IN_PROGRESS: frozenset({TicketStatus.TESTING}),
    TicketStatus.TESTING: frozenset(
        {TicketStatus.IN_PROGRESS, TicketStatus.DIFF_PENDING}
    ),
    TicketStatus.DIFF_PENDING: frozenset(
        {TicketStatus.IN_PROGRESS, TicketStatus.REVIEW}
    ),
    TicketStatus.REVIEW: frozenset(
        {TicketStatus.BACKLOG, TicketStatus.AWAITING_ACCEPTANCE}
    ),
    TicketStatus.AWAITING_ACCEPTANCE: frozenset(
        {TicketStatus.BACKLOG, TicketStatus.DONE}
    ),
    TicketStatus.DONE: frozenset(),
    TicketStatus.BLOCKED: frozenset(),
    TicketStatus.ABANDONED: frozenset(),
    TicketStatus.SPLIT: frozenset(),
}


class InvalidTransitionError(ValueError):
    """Raised when a ticket status change is not allowed by the state machine."""


@dataclass(frozen=True)
class TransitionResult:
    ticket: Ticket
    from_status: TicketStatus
    to_status: TicketStatus
    escalated: bool = False
    escalated_to: str | None = None


class TicketStateMachine:
    def can_transition(
        self,
        from_status: TicketStatus | str,
        to_status: TicketStatus | str,
    ) -> bool:
        source = TicketStatus(from_status)
        target = TicketStatus(to_status)
        return target in LEGAL_TRANSITIONS[source]

    def transition(
        self,
        ticket: Ticket,
        to_status: TicketStatus | str,
    ) -> TransitionResult:
        source = TicketStatus(ticket.status)
        target = TicketStatus(to_status)
        if not self.can_transition(source, target):
            raise InvalidTransitionError(
                f"Illegal ticket transition: {source.value} -> {target.value}"
            )

        updated_ticket = _replace_ticket_fields(ticket, status=target.value)
        return TransitionResult(
            ticket=updated_ticket,
            from_status=source,
            to_status=target,
        )

    def record_test_failure(self, ticket: Ticket) -> TransitionResult:
        source = TicketStatus(ticket.status)
        if source != TicketStatus.TESTING:
            raise InvalidTransitionError(
                "Test failures can only be recorded while a ticket is in testing"
            )

        ticket_json = ticket.to_dict()
        execution = ticket_json["execution"]
        attempts = execution.get("attempts", 0) + 1
        execution["attempts"] = attempts

        retry_budget = execution["retry_budget"]
        if attempts <= retry_budget:
            ticket_json["status"] = TicketStatus.IN_PROGRESS.value
            updated_ticket = Ticket.from_dict(ticket_json)
            return TransitionResult(
                ticket=updated_ticket,
                from_status=source,
                to_status=TicketStatus.IN_PROGRESS,
            )

        escalated_to = execution.get("escalate_to", "tech_lead")
        metadata = ticket_json.setdefault("metadata", {})
        metadata["escalated_to"] = escalated_to
        metadata["escalated_at"] = datetime.now(UTC).isoformat()
        metadata["escalation_reason"] = "retry_budget_exhausted"
        ticket_json["status"] = TicketStatus.BLOCKED.value
        updated_ticket = Ticket.from_dict(ticket_json)
        return TransitionResult(
            ticket=updated_ticket,
            from_status=source,
            to_status=TicketStatus.BLOCKED,
            escalated=True,
            escalated_to=escalated_to,
        )


class TicketStateService:
    def __init__(
        self,
        repository: TicketRepository,
        state_machine: TicketStateMachine | None = None,
    ) -> None:
        self.repository = repository
        self.state_machine = state_machine or TicketStateMachine()

    def move(
        self,
        ticket_id: str,
        to_status: TicketStatus | str,
    ) -> TransitionResult:
        ticket = self._require_ticket(ticket_id)
        result = self.state_machine.transition(ticket, to_status)
        saved = self.repository.save(result.ticket)
        saved_result = _with_saved_ticket(result, saved)
        self._append_done_report(saved_result)
        return saved_result

    def record_test_failure(self, ticket_id: str) -> TransitionResult:
        ticket = self._require_ticket(ticket_id)
        result = self.state_machine.record_test_failure(ticket)
        return _with_saved_ticket(result, self.repository.save(result.ticket))

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _append_done_report(self, result: TransitionResult) -> None:
        if result.to_status not in {TicketStatus.REVIEW, TicketStatus.DONE}:
            return
        project_id = _ticket_project_id(result.ticket)
        chat_repository = ChatRepository(self.repository.connection)
        segment_id = chat_repository.active_segment_id(project_id)
        label = result.ticket.title or result.ticket.id
        state = "Done" if result.to_status == TicketStatus.DONE else "Review"
        chat_repository.append_message(
            project_id=project_id,
            role="system_report",
            text=f"{result.ticket.id} done - in {state}: {label}",
            segment_id=segment_id,
            ticket_id=result.ticket.id,
            report_kind="done",
        )
        metadata = result.ticket.metadata.model_dump(mode="json") if result.ticket.metadata else {}
        RunEventRepository(self.repository.connection).append_run_event(
            project_id=project_id,
            requirement_id=_ticket_requirement_id(result.ticket),
            ticket_id=result.ticket.id,
            run_id=_string_or_none(metadata.get("last_run_id")),
            event_type="report",
            payload={
                "report_kind": "done",
                "state": state.lower(),
                "from_status": result.from_status.value,
                "to_status": result.to_status.value,
                "title": label,
            },
        )


def _replace_ticket_fields(ticket: Ticket, **fields: object) -> Ticket:
    ticket_json = ticket.to_dict()
    ticket_json.update(fields)
    return Ticket.from_dict(ticket_json)


def _with_saved_ticket(result: TransitionResult, ticket: Ticket) -> TransitionResult:
    return TransitionResult(
        ticket=ticket,
        from_status=result.from_status,
        to_status=result.to_status,
        escalated=result.escalated,
        escalated_to=result.escalated_to,
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


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
