import pytest

from orchestrator.models.ticket import SchemaValidationError, Ticket


def test_example_ticket_loads_and_round_trips(fresh_ticket_dict) -> None:
    ticket = Ticket.from_dict(fresh_ticket_dict)

    assert ticket.id == "T-012"
    assert ticket.to_dict()["definition_of_done"]["tests"][0]["expect"] == "pass"


def test_missing_required_field_has_clear_error(fresh_ticket_dict) -> None:
    fresh_ticket_dict.pop("task")

    with pytest.raises(Exception) as exc_info:
        Ticket.from_dict(fresh_ticket_dict)

    assert "task" in str(exc_info.value)


def test_schema_rejects_additional_properties(fresh_ticket_dict) -> None:
    fresh_ticket_dict["unexpected"] = True

    with pytest.raises(SchemaValidationError) as exc_info:
        Ticket.from_dict(fresh_ticket_dict)

    assert "unexpected" in str(exc_info.value)
