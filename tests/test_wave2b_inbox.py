from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.api import get_settings
from orchestrator.db.sqlite import (
    ChatRepository,
    NotificationRepository,
    RunEventRepository,
    TicketRepository,
    connect,
)
from orchestrator.main import app
from orchestrator.models.ticket import Ticket


def test_notifications_dedupe_chat_report_and_run_event_sources(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    _create_ticket(connection, fresh_ticket_dict, "T-700", project_id="default")
    chat = ChatRepository(connection)
    segment_id = chat.active_segment_id("default")

    chat.append_message(
        project_id="default",
        role="system_report",
        text="T-700 blocked - needs a decision: ticket_blocked",
        segment_id=segment_id,
        ticket_id="T-700",
        report_kind="blocked",
    )
    RunEventRepository(connection).append_run_event(
        project_id="default",
        ticket_id="T-700",
        event_type="report",
        payload={
            "report_kind": "blocked",
            "reason": "ticket_blocked",
            "status": "blocked",
        },
    )

    notifications = NotificationRepository(connection).list(project_id="default")
    assert len(notifications) == 1
    assert notifications[0].kind == "blocked"
    assert notifications[0].ticket_id == "T-700"
    assert notifications[0].unread is True


def test_notifications_endpoints_read_read_all_and_cross_project_counts(
    tmp_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    connection = connect(db_path)
    _create_ticket(connection, fresh_ticket_dict, "T-701", project_id="default")
    _create_ticket(connection, fresh_ticket_dict, "T-702", project_id="P-2")
    repo = NotificationRepository(connection)
    first = repo.record_notification(
        project_id="default",
        ticket_id="T-701",
        kind="needs_you",
        title="T-701 needs you: review",
        dedupe_key="default:needs_you:T-701:review",
    )
    repo.record_notification(
        project_id="P-2",
        ticket_id="T-702",
        kind="done",
        title="T-702 done",
        dedupe_key="P-2:done:T-702:done",
    )

    app.dependency_overrides[get_settings] = lambda: _settings(db_path)
    try:
        client = TestClient(app)
        listed = client.get("/api/notifications").json()
        assert listed["unread_count"] == {"total": 2, "by_project": {"P-2": 1, "default": 1}}
        assert {item["project_id"] for item in listed["notifications"]} == {"default", "P-2"}

        read = client.post(f"/api/notifications/{first.id}/read")
        assert read.status_code == 200
        assert read.json()["notification"]["read_at"] is not None
        assert read.json()["unread_count"] == {"total": 1, "by_project": {"P-2": 1}}

        unread_default = client.get("/api/notifications?project_id=default&unread_only=true").json()
        assert unread_default["notifications"] == []

        read_all = client.post("/api/notifications/read-all?project_id=P-2")
        assert read_all.status_code == 200
        assert read_all.json()["updated"] == 1
        assert read_all.json()["unread_count"] == {"total": 0, "by_project": {}}
    finally:
        app.dependency_overrides.clear()


def test_notification_titles_are_redacted(tmp_path: Path) -> None:
    repo = NotificationRepository(connect(tmp_path / "haao.sqlite3"))

    notification = repo.record_notification(
        project_id="default",
        kind="needs_you",
        title="Review failed with sk-secret123456789",
        dedupe_key="default:needs_you:project:redacted",
    )

    assert notification.title == "Review failed with ***redacted***"


def _create_ticket(connection, fresh_ticket_dict: dict, ticket_id: str, *, project_id: str) -> None:
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["id"] = ticket_id
    payload["metadata"] = {"project_id": project_id}
    TicketRepository(connection, project_id=project_id).create(Ticket.from_dict(payload))


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
