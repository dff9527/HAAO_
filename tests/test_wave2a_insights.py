from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.api import get_settings
from orchestrator.db.sqlite import RunEventRepository, TicketRepository, connect
from orchestrator.main import app
from orchestrator.models.ticket import Ticket


def test_insights_endpoint_aggregates_run_events_and_tickets(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    tickets = TicketRepository(connection)
    now = datetime.now(UTC)
    _create_ticket(
        tickets,
        fresh_ticket_dict,
        ticket_id="T-100",
        ticket_type="feature",
        created_at=now - timedelta(days=2),
        done_at=now - timedelta(days=1),
        human_override=True,
    )
    _create_ticket(
        tickets,
        fresh_ticket_dict,
        ticket_id="T-101",
        ticket_type="bugfix",
        created_at=now - timedelta(hours=6),
        done_at=now - timedelta(hours=1),
        human_override=False,
    )
    _create_ticket(
        tickets,
        fresh_ticket_dict,
        ticket_id="T-102",
        ticket_type="chore",
        created_at=now - timedelta(days=45),
        done_at=now - timedelta(days=40),
        human_override=False,
    )
    events = RunEventRepository(connection)
    _seed_run(
        events,
        run_id="RUN-1",
        ticket_id="T-100",
        model_id="qwen3-coder-next",
        ts=now - timedelta(days=1, hours=1),
        passed=True,
        cost_status="unknown",
        cost_usd=0.0,
    )
    _seed_run(
        events,
        run_id="RUN-2",
        ticket_id="T-101",
        model_id="anthropic:claude-sonnet-4-6",
        ts=now - timedelta(hours=2),
        passed=False,
        cost_status="actual",
        cost_usd=0.02,
        retry=True,
        escalation=True,
    )
    events.append_run_event(
        project_id="default",
        ticket_id="T-101",
        run_id="RUN-2",
        event_type="model_call",
        ts=(now - timedelta(hours=2, minutes=5)).isoformat(),
        model_id="anthropic:claude-sonnet-4-6",
        input_tokens=80,
        output_tokens=20,
        cost_usd=0.01,
        cost_status="estimated",
        payload={"used_cloud_usage": True},
    )

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        response = TestClient(app).get("/api/insights?project_id=default&range=30d")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["throughput"]["total_done"] == 2
    assert body["cycle_time"]["sample_size"] == 2
    assert body["cycle_time"]["avg_hours"] == 14.5
    assert body["cycle_time"]["median_hours"] == 14.5
    assert body["escalation_rate"] == {"runs": 2, "escalations": 1, "rate": 0.5}
    assert body["local_vs_cloud"]["local"]["count"] == 1
    assert body["local_vs_cloud"]["cloud"]["count"] == 2
    assert body["cost"]["total_usd"] == 0.03
    assert body["cost"]["by_status"] == {
        "actual": 0.02,
        "estimated": 0.01,
        "unknown": 0.0,
    }

    scorecard = {item["model_id"]: item for item in body["model_scorecard"]}
    local = scorecard["qwen3-coder-next"]
    assert local["sample_size"] == 1
    assert local["success_rate"] == 1.0
    assert local["task_type_mix"] == {"feature": 1}
    assert local["human_override_count"] == 1

    cloud = scorecard["anthropic:claude-sonnet-4-6"]
    assert cloud["sample_size"] == 1
    assert cloud["success_rate"] == 0.0
    assert cloud["retries"] == 1
    assert cloud["escalations"] == 1
    assert cloud["cost_by_status"] == {
        "actual": 0.02,
        "estimated": 0.01,
        "unknown": 0.0,
    }
    assert cloud["task_type_mix"] == {"bugfix": 1}
    assert cloud["human_override_count"] == 0


def test_insights_empty_project_returns_zeroes(tmp_path: Path) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connect(db_path).close()

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        response = TestClient(app).get("/api/insights?project_id=P-empty&range=all")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["throughput"] == {"total_done": 0, "series": []}
    assert body["cycle_time"] == {"sample_size": 0, "avg_hours": 0.0, "median_hours": 0.0}
    assert body["escalation_rate"] == {"runs": 0, "escalations": 0, "rate": 0.0}
    assert body["local_vs_cloud"]["total_model_calls"] == 0
    assert body["cost"]["by_status"] == {"actual": 0.0, "estimated": 0.0, "unknown": 0.0}
    assert body["model_scorecard"] == []


def _create_ticket(
    repository: TicketRepository,
    fresh_ticket_dict: dict,
    *,
    ticket_id: str,
    ticket_type: str,
    created_at: datetime,
    done_at: datetime,
    human_override: bool,
) -> None:
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["id"] = ticket_id
    payload["type"] = ticket_type
    payload["status"] = "done"
    payload["metadata"] = {
        "project_id": "default",
        "created_at": created_at.isoformat(),
        "accepted_at": done_at.isoformat(),
    }
    if human_override:
        payload["metadata"]["approved_by"] = "product-owner"
        payload["metadata"]["accepted_by"] = "product-owner"
    repository.create(Ticket.from_dict(payload))


def _seed_run(
    events: RunEventRepository,
    *,
    run_id: str,
    ticket_id: str,
    model_id: str,
    ts: datetime,
    passed: bool,
    cost_status: str,
    cost_usd: float,
    retry: bool = False,
    escalation: bool = False,
) -> None:
    events.append_run_event(
        project_id="default",
        ticket_id=ticket_id,
        run_id=run_id,
        event_type="run_started",
        ts=ts.isoformat(),
        model_id=model_id,
    )
    events.append_run_event(
        project_id="default",
        ticket_id=ticket_id,
        run_id=run_id,
        event_type="model_call",
        ts=(ts + timedelta(minutes=1)).isoformat(),
        model_id=model_id,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost_usd,
        cost_status=cost_status,
        payload={"used_cloud_usage": cost_status != "unknown"},
    )
    if retry:
        events.append_run_event(
            project_id="default",
            ticket_id=ticket_id,
            run_id=run_id,
            event_type="retry",
            ts=(ts + timedelta(minutes=2)).isoformat(),
            model_id=model_id,
            payload={"reason": "dod_failed"},
        )
    if escalation:
        events.append_run_event(
            project_id="default",
            ticket_id=ticket_id,
            run_id=run_id,
            event_type="escalation",
            ts=(ts + timedelta(minutes=3)).isoformat(),
            model_id=model_id,
            payload={"reason": "retry_budget_exhausted"},
        )
    events.append_run_event(
        project_id="default",
        ticket_id=ticket_id,
        run_id=run_id,
        event_type="run_finished",
        ts=(ts + timedelta(minutes=4)).isoformat(),
        model_id=model_id,
        payload={"passed": passed},
    )


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
