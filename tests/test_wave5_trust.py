from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import get_settings
from orchestrator.db.sqlite import (
    PromptVersionRepository,
    RunEventRepository,
    TicketRepository,
    connect,
)
from orchestrator.diff_review import DiffReviewService
from orchestrator.execution_safety import DiffScopeError, derive_diff_stats
from orchestrator.golden_tasks import DEFAULT_GOLDEN_FIXTURE, run_golden_task_regression
from orchestrator.main import app
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.state_machine import InvalidTransitionError, TicketStateMachine, TicketStateService
from tests.conftest import init_git_repo


def test_abandoned_is_terminal_and_legacy_metadata_rows_migrate_on_read(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    TicketRepository(connection)
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["id"] = "T-501"
    payload["status"] = "blocked"
    payload["metadata"] = {"project_id": "default", "abandoned": True}
    connection.execute(
        """
        INSERT INTO tickets (id, project_id, status, ticket_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("T-501", "default", "blocked", json.dumps(payload), "2026-06-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"),
    )
    connection.commit()

    repository = TicketRepository(connection)
    ticket = repository.get("T-501")

    assert ticket is not None
    assert ticket.status == TicketStatus.ABANDONED
    assert TicketStateMachine().can_transition(TicketStatus.ABANDONED, TicketStatus.READY) is False
    with pytest.raises(InvalidTransitionError):
        TicketStateService(repository).move("T-501", TicketStatus.READY)


def test_split_creates_child_tickets_and_audit_events(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    repository = TicketRepository(connection)
    parent = _ticket(
        fresh_ticket_dict,
        ticket_id="T-502",
        status="blocked",
        target_files=["api.py", "ui.py"],
    )
    repository.create(parent)

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        response = TestClient(app).post(
            "/api/tickets/T-502/split",
            json={"feedback": "Split API and UI work."},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["parent_id"] == "T-502"
    assert len(body["child_ticket_ids"]) == 2
    assert body["ticket"]["status"] == "split"
    assert body["ticket"]["metadata"]["child_ticket_ids"] == body["child_ticket_ids"]

    children = [repository.get(ticket_id) for ticket_id in body["child_ticket_ids"]]
    assert [child.status for child in children if child is not None] == ["backlog", "backlog"]
    assert [child.metadata.model_dump(mode="json")["parent_ticket_id"] for child in children if child] == ["T-502", "T-502"]
    assert [child.task.target_files for child in children if child] == [["api.py"], ["ui.py"]]

    events = RunEventRepository(connection).list_run_events("default")
    actions = [event.payload.get("action") for event in events if event.payload]
    assert "split" in actions
    assert actions.count("split_from") == 2


def test_diff_stats_and_diff_scope_reject_event(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    init_git_repo(repo)
    connection = connect(tmp_path / "haao.sqlite3")
    repository = TicketRepository(connection)
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
    )
    ticket = _ticket(
        fresh_ticket_dict,
        ticket_id="T-503",
        status="diff_pending",
        target_files=["calc.py"],
        result={"outcome": "success", "diff": diff},
    )
    repository.create(ticket)

    assert derive_diff_stats(diff, ["calc.py"]) == {
        "files_touched": 1,
        "lines_added": 1,
        "lines_removed": 1,
        "out_of_scope_files": ["app.py"],
    }
    reloaded = repository.get("T-503")
    assert reloaded.result.diff_stats["out_of_scope_files"] == ["app.py"]

    service = DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=repo,
    )
    with pytest.raises(DiffScopeError):
        service.approve_diff("T-503")

    events = RunEventRepository(connection).list_run_events("default", ticket_id="T-503")
    event = next(event for event in events if event.event_type == "diff_scope_reject")
    assert event.payload["kind"] == "diff_scope_reject"
    assert event.payload["ticket_id"] == "T-503"
    assert event.payload["detail"].startswith("Diff touches files outside")


def test_safety_run_event_contract_fields_are_frozen(tmp_path: Path) -> None:
    repository = RunEventRepository(connect(tmp_path / "haao.sqlite3"))
    for event_type in ("egress_attempt", "rollback"):
        event = repository.append_run_event(
            project_id="default",
            ticket_id="T-504",
            run_id="RUN-1",
            event_type=event_type,
            payload={"reason": "test"},
        )
        assert event.payload["kind"] == event_type
        assert event.payload["ticket_id"] == "T-504"
        assert event.payload["run_id"] == "RUN-1"
        assert event.payload["detail"] == "test"
        assert event.payload["ts"]


def test_golden_task_guard_passes_and_fails_on_mutated_fixture(tmp_path: Path) -> None:
    result = run_golden_task_regression()
    assert result.passed is True
    assert result.task_count == 1

    mutated = json.loads(DEFAULT_GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    mutated["tasks"][0]["expect"]["tickets"][0]["target_files"] = ["wrong.py"]
    mutated_path = tmp_path / "mutated_golden.json"
    mutated_path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(AssertionError, match="drifted"):
        run_golden_task_regression(mutated_path)


def test_prompt_versions_lookup_records_decompose_template(tmp_path: Path) -> None:
    run_golden_task_regression()
    connection = connect(tmp_path / "haao.sqlite3")
    repository = PromptVersionRepository(connection)
    record = repository.record(prompt_id="unit:sha256:abc", template_hash="abc")
    assert record.id == "unit:sha256:abc"
    assert repository.get("unit:sha256:abc").template_hash == "abc"


def _ticket(
    base: dict,
    *,
    ticket_id: str,
    status: str,
    target_files: list[str] | None = None,
    result: dict | None = None,
) -> Ticket:
    payload = copy.deepcopy(base)
    payload["id"] = ticket_id
    payload["status"] = status
    payload["task"]["target_files"] = target_files or ["app.py"]
    payload["context"]["files"] = [
        {"path": path, "content": "value = 1\n"}
        for path in payload["task"]["target_files"]
    ]
    payload["result"] = result or {"outcome": "pending"}
    payload["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
    payload["metadata"] = {"project_id": "default", "requirement_id": "R-500"}
    return Ticket.from_dict(payload)


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
