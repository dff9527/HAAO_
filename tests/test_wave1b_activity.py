from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from clients.lmstudio import ChatMessage
from orchestrator.api import get_settings
from orchestrator.db.sqlite import RunEventRepository, TicketRepository, connect
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.policies import record_egress_attempt
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


class SequentialModel:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        return self.outputs.pop(0)


def test_full_ticket_run_emits_ordered_redacted_activity_stream(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repo_root = _repo(tmp_path)
    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    tickets.create(
        Ticket.from_dict(
            _ticket(
                fresh_ticket_dict,
                status="ready",
                retry_budget=1,
                command=(
                    f"{sys.executable} -c \"import calc; print('sk-secret123456789'); "
                    "assert calc.add_one(1) == 2\""
                ),
            )
        )
    )

    result = ExecutionLoop(
        tickets,
        TicketStateService(tickets),
        SequentialModel(
            [
                "def add_one(value):\n    return value\n",
                "def add_one(value):\n    return value + 1\n",
            ]
        ),
        repo_root=repo_root,
        test_runner=TestRunner(cwd=repo_root),
    ).run_ticket("T-510")

    assert result.passed is True
    events = RunEventRepository(connection).list_run_events("default", ticket_id="T-510")
    event_types = [event.event_type for event in events]
    assert event_types[0] == "run_started"
    assert event_types[-1] == "run_finished"
    assert event_types.count("model_call") == 2
    assert event_types.count("diff_produced") == 2
    assert event_types.count("dod_check") == 2
    assert "retry" in event_types
    assert "report" in event_types
    run_ids = {event.run_id for event in events}
    assert len(run_ids) == 1
    assert "sk-secret123456789" not in str([event.to_dict() for event in events])
    dod_events = [event for event in events if event.event_type == "dod_check"]
    assert "***redacted***" in dod_events[0].payload["output_tail"]


def test_blocked_ticket_run_emits_escalation_and_report(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repo_root = _repo(tmp_path)
    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    tickets.create(
        Ticket.from_dict(
            _ticket(
                fresh_ticket_dict,
                status="ready",
                retry_budget=0,
                command=f"{sys.executable} -c \"import calc; assert calc.add_one(1) == 2\"",
            )
        )
    )

    result = ExecutionLoop(
        tickets,
        TicketStateService(tickets),
        SequentialModel(["def add_one(value):\n    return value\n"]),
        repo_root=repo_root,
        test_runner=TestRunner(cwd=repo_root),
    ).run_ticket("T-510")

    assert result.escalated is True
    events = RunEventRepository(connection).list_run_events("default", ticket_id="T-510")
    event_types = [event.event_type for event in events]
    assert "escalation" in event_types
    assert "report" in event_types
    escalation = next(event for event in events if event.event_type == "escalation")
    assert escalation.payload["reason"] == "retry_budget_exhausted"
    assert {event.run_id for event in events} == {events[0].run_id}


def test_egress_producer_and_run_events_endpoint_cursor_ticket_filter(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    repo = RunEventRepository(connection)
    first = repo.append_run_event(
        project_id="P-1",
        ticket_id="T-001",
        run_id="RUN-1",
        event_type="run_started",
    )
    repo.append_run_event(
        project_id="P-1",
        ticket_id="T-002",
        run_id="RUN-2",
        event_type="run_started",
    )
    record_egress_attempt(
        connection,
        project_id="P-1",
        ticket_id="T-001",
        run_id="RUN-1",
        destination="https://api.example.test/sk-secret123456789",
        command="python network_probe.py",
    )

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        response = TestClient(app).get(
            f"/run-events?project_id=P-1&after={first.id}&ticket_id=T-001"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["event_type"] for event in events] == ["egress_attempt"]
    assert events[0]["run_id"] == "RUN-1"
    assert "sk-secret123456789" not in str(events)


def _repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(repo_root)
    return repo_root


def _ticket(
    fresh_ticket_dict: dict,
    *,
    status: str,
    retry_budget: int,
    command: str,
) -> dict:
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["id"] = "T-510"
    payload["title"] = "Activity stream ticket"
    payload["status"] = status
    payload["task"]["target_files"] = ["calc.py"]
    payload["context"]["files"] = [{"path": "calc.py", "content": ""}]
    payload["definition_of_done"]["tests"] = [
        {"command": command, "expect": "pass", "timeout_sec": 120}
    ]
    payload["execution"]["retry_budget"] = retry_budget
    payload["execution"]["attempts"] = 0
    payload["metadata"] = {"project_id": "default", "requirement_id": "R-510"}
    return payload


def _settings(db_path: Path):
    return SimpleNamespace(
        claude_api_key="",
        openai_api_key="",
        gemini_api_key="",
        lmstudio_base_url="http://localhost:1234/v1",
        local_max_output_tokens=4096,
        local_patch_mode_threshold_tokens=2048,
        database_url=f"sqlite:///{db_path}",
        claude_model="claude-sonnet-4-6",
        haao_api_token="",
    )
