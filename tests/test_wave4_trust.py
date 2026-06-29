from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.api import get_settings
from orchestrator.db.sqlite import RequirementRepository, RunEventRepository, TicketRepository, connect
from orchestrator.main import app
from orchestrator.models.requirement import Requirement, RequirementStatus
from orchestrator.models.ticket import Ticket


def test_wave4_decisions_and_gate1_signals_are_derived(tmp_path: Path, fresh_ticket_dict: dict) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    tickets = TicketRepository(connection)
    risky = _ticket(
        fresh_ticket_dict,
        ticket_id="T-400",
        status="backlog",
        target_files=[".env", "src/a.py", "src/b.py", "src/c.py"],
        assigned_model="anthropic:claude-sonnet-4-6",
        needs_approval=True,
    )
    tickets.create(risky)
    requirement = Requirement(
        id="R-400",
        project_id="default",
        prompt="Add a risky auth change",
        status=RequirementStatus.PREVIEW_READY,
        priority="high",
        attachments=[{"type": "image", "value": "attachment:image-1"}],
        proposed_tickets=[risky.to_dict()],
    )
    RequirementRepository(connection, project_id="default").create(requirement)

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        signal_response = client.get("/api/tickets/T-400/signals")
        decisions_response = client.get("/api/decisions?project_id=default")
        requirement_response = client.get("/api/requirements/R-400/signals?project_id=default")
    finally:
        app.dependency_overrides.clear()

    assert signal_response.status_code == 200
    signals = signal_response.json()["signals"]
    assert signals["derived_only"] is True
    assert signals["risk"]["level"] == "high"
    assert ".env" in signals["affected_files"]
    assert {flag["id"] for flag in signals["cloud_privacy_flags"]} >= {
        "cloud_execution_model",
        "sensitive_file_target",
    }

    assert requirement_response.status_code == 200
    requirement_signals = requirement_response.json()["signals"]
    assert requirement_signals["highest_risk"] == "high"
    assert "image_attachment_cloud_analysis" in {
        flag["id"] for flag in requirement_signals["cloud_privacy_flags"]
    }

    assert decisions_response.status_code == 200
    decisions = decisions_response.json()
    assert decisions["counts"]["gate1_scope"] == 2
    assert decisions["counts"]["high_risk"] == 1


def test_wave4_acceptance_summary_and_actions(tmp_path: Path, fresh_ticket_dict: dict) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    repository = TicketRepository(connection)
    repository.create(
        _ticket(
            fresh_ticket_dict,
            ticket_id="T-401",
            status="awaiting_acceptance",
            result={"outcome": "success", "diff": "diff --git a/app.py b/app.py"},
            audit={"verdict": "approved", "feedback": "", "reviewed_by": "gatekeeper"},
        )
    )
    repository.create(_ticket(fresh_ticket_dict, ticket_id="T-402", status="blocked"))

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        summary_response = client.get("/api/tickets/T-401/acceptance-summary")
        split_response = client.post("/api/tickets/T-401/split", json={"feedback": "Split into API and UI tickets"})
        abandon_response = client.post("/api/tickets/T-402/abandon", json={"reason": "No longer needed"})
        decisions_response = client.get("/api/decisions?project_id=default")
    finally:
        app.dependency_overrides.clear()

    assert summary_response.status_code == 200
    summary = summary_response.json()["summary"]
    assert summary["recommendation"] == "ready"
    assert summary["pr"]["ready"] is True
    assert all(check["passed"] for check in summary["checks"] if check["severity"] == "critical")

    assert split_response.status_code == 200
    split_body = split_response.json()
    split_ticket = split_body["ticket"]
    assert split_ticket["status"] == "split"
    assert split_ticket["metadata"]["split_requested"] is True
    assert split_ticket["metadata"]["needs_approval"] is False
    assert split_body["child_ticket_ids"]

    assert abandon_response.status_code == 200
    abandoned = abandon_response.json()["ticket"]
    assert abandoned["status"] == "abandoned"
    assert abandoned["metadata"]["abandoned"] is True
    events = RunEventRepository(connection).list_run_events("default")
    assert {event.payload.get("action") for event in events if event.payload} >= {"split", "abandon"}

    decisions = decisions_response.json()
    blocked_items = decisions["counts"]["blocked"]
    assert blocked_items == 0


def test_wave4_insights_adds_time_to_first_pr_and_roi(tmp_path: Path, fresh_ticket_dict: dict) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    repository = TicketRepository(connection)
    now = datetime.now(UTC)
    repository.create(
        _ticket(
            fresh_ticket_dict,
            ticket_id="T-403",
            status="done",
            metadata={
                "project_id": "default",
                "created_at": (now - timedelta(hours=4)).isoformat(),
                "accepted_at": (now - timedelta(hours=1)).isoformat(),
                "pr_url": "https://github.com/acme/widgets/pull/3",
                "pr_updated_at": (now - timedelta(hours=2)).isoformat(),
            },
        )
    )
    RunEventRepository(connection).append_run_event(
        project_id="default",
        ticket_id="T-403",
        event_type="model_call",
        ts=(now - timedelta(hours=3)).isoformat(),
        model_id="anthropic:claude-sonnet-4-6",
        cost_usd=0.25,
        cost_status="actual",
        payload={"used_cloud_usage": True},
    )

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        response = TestClient(app).get("/api/insights?project_id=default&range=30d")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["time_to_first_pr"]["sample_size"] == 1
    assert body["time_to_first_pr"]["median_hours"] == 2.0
    assert body["roi"]["done_tickets"] == 1
    assert body["roi"]["cloud_cost_usd"] == 0.25
    assert body["roi"]["estimated_net_value_usd"] == 149.75


def _ticket(
    base: dict,
    *,
    ticket_id: str,
    status: str,
    target_files: list[str] | None = None,
    assigned_model: str = "qwen3-coder-next",
    needs_approval: bool | None = None,
    result: dict | None = None,
    audit: dict | None = None,
    metadata: dict | None = None,
) -> Ticket:
    payload = copy.deepcopy(base)
    payload["id"] = ticket_id
    payload["status"] = status
    payload["task"]["target_files"] = target_files or ["app.py"]
    payload["context"]["files"] = [
        {"path": path, "content": "value = 1\n", "truncated": path.endswith("c.py")}
        for path in payload["task"]["target_files"]
    ]
    payload["execution"]["assigned_model"] = assigned_model
    payload["result"] = result or {"outcome": "pending"}
    payload["audit"] = audit or {"verdict": "pending", "feedback": "", "reviewed_by": ""}
    payload["metadata"] = metadata or {"project_id": "default", "requirement_id": "R-400"}
    if needs_approval is not None:
        payload["metadata"]["needs_approval"] = needs_approval
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
