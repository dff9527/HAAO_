import copy
from itertools import product

import pytest

from orchestrator.db.sqlite import TicketRepository, connect
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.state_machine import (
    LEGAL_TRANSITIONS,
    InvalidTransitionError,
    TicketStateMachine,
    TicketStateService,
)


ALL_STATUSES = list(TicketStatus)


def make_ticket(ticket_dict, status: TicketStatus | str, attempts: int = 0) -> Ticket:
    data = copy.deepcopy(ticket_dict)
    data["status"] = TicketStatus(status).value
    data["execution"]["attempts"] = attempts
    return Ticket.from_dict(data)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (from_status, to_status)
        for from_status, targets in LEGAL_TRANSITIONS.items()
        for to_status in targets
    ],
)
def test_legal_transitions_are_allowed(
    fresh_ticket_dict,
    from_status: TicketStatus,
    to_status: TicketStatus,
) -> None:
    ticket = make_ticket(fresh_ticket_dict, from_status)
    result = TicketStateMachine().transition(ticket, to_status)

    assert result.from_status == from_status
    assert result.to_status == to_status
    assert result.ticket.status == to_status.value


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (from_status, to_status)
        for from_status, to_status in product(ALL_STATUSES, ALL_STATUSES)
        if to_status not in LEGAL_TRANSITIONS[from_status]
    ],
)
def test_illegal_transitions_are_rejected(
    fresh_ticket_dict,
    from_status: TicketStatus,
    to_status: TicketStatus,
) -> None:
    ticket = make_ticket(fresh_ticket_dict, from_status)

    with pytest.raises(InvalidTransitionError):
        TicketStateMachine().transition(ticket, to_status)


def test_test_failure_with_retry_budget_returns_to_in_progress(
    fresh_ticket_dict,
) -> None:
    ticket = make_ticket(fresh_ticket_dict, TicketStatus.TESTING, attempts=2)

    result = TicketStateMachine().record_test_failure(ticket)

    assert result.ticket.status == "in_progress"
    assert result.ticket.execution.attempts == 3
    assert result.escalated is False


def test_test_failure_after_retry_budget_escalates(
    fresh_ticket_dict,
) -> None:
    ticket = make_ticket(fresh_ticket_dict, TicketStatus.TESTING, attempts=3)

    result = TicketStateMachine().record_test_failure(ticket)

    assert result.ticket.status == "blocked"
    assert result.ticket.execution.attempts == 4
    assert result.escalated is True
    assert result.escalated_to == "tech_lead"
    assert result.ticket.metadata.escalated_to == "tech_lead"
    assert result.ticket.metadata.escalation_reason == "retry_budget_exhausted"


def test_zero_retry_budget_escalates_on_first_failure(fresh_ticket_dict) -> None:
    fresh_ticket_dict["execution"]["retry_budget"] = 0
    ticket = make_ticket(fresh_ticket_dict, TicketStatus.TESTING)

    result = TicketStateMachine().record_test_failure(ticket)

    assert result.ticket.status == "blocked"
    assert result.ticket.execution.attempts == 1
    assert result.escalated is True


def test_test_failure_requires_testing_status(fresh_ticket_dict) -> None:
    ticket = make_ticket(fresh_ticket_dict, TicketStatus.IN_PROGRESS)

    with pytest.raises(InvalidTransitionError):
        TicketStateMachine().record_test_failure(ticket)


def test_state_service_persists_transitions_and_retry_metadata(
    tmp_path,
    fresh_ticket_dict,
) -> None:
    fresh_ticket_dict["status"] = "backlog"
    repository = TicketRepository(connect(tmp_path / "haao.sqlite3"))
    repository.create(Ticket.from_dict(fresh_ticket_dict))
    service = TicketStateService(repository)

    assert service.move("T-012", "ready").ticket.status == "ready"
    assert repository.get("T-012").status == "ready"

    repository.update_status("T-012", "testing")
    service.record_test_failure("T-012")

    persisted = repository.get("T-012")
    assert persisted.status == "in_progress"
    assert persisted.execution.attempts == 2
