from __future__ import annotations

import copy
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.api import get_chat_repository, get_chat_service
from orchestrator.chat_flow import ChatMessage, ChatTurnResult
from orchestrator.db.sqlite import ChatRepository, SettingsRepository, TicketRepository, connect
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.notifications import NotificationService, set_notification_webhook
from orchestrator.state_machine import TicketStateService


def _ticket(ticket_dict: dict, *, status: str = "backlog", project_id: str = "default") -> Ticket:
    payload = copy.deepcopy(ticket_dict)
    payload["status"] = status
    payload.setdefault("metadata", {})["project_id"] = project_id
    return Ticket.from_dict(payload)


def test_chat_repository_crud_cursor_and_single_active_segment(tmp_path) -> None:
    repository = ChatRepository(connect(tmp_path / "haao.sqlite3"))

    default_segment = repository.active_segment_id("P-1")
    first = repository.append_message(
        project_id="P-1",
        role="user",
        text="hello",
        segment_id=default_segment,
    )
    second = repository.append_message(
        project_id="P-1",
        role="agent",
        text="hi",
        segment_id=default_segment,
    )

    assert [message.id for message in repository.list_messages("P-1")] == [first.id, second.id]
    assert [message.id for message in repository.list_messages("P-1", after=first.id)] == [second.id]
    assert [message.id for message in repository.list_messages("P-1", limit=1)] == [second.id]

    repository.set_summary("P-1", default_segment, "User wants backend chat.")
    assert repository.get_summary("P-1", default_segment) == "User wants backend chat."

    next_segment = repository.create_segment(project_id="P-1", title="Sprint 2")
    assert repository.active_segment_id("P-1") == next_segment.id
    segments = repository.list_segments("P-1")
    assert [segment.is_active for segment in segments] == [False, True]


def test_state_transition_to_review_writes_done_report(tmp_path, fresh_ticket_dict) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    ticket_repository = TicketRepository(connection)
    chat_repository = ChatRepository(connection)
    ticket_repository.create(_ticket(fresh_ticket_dict, status="diff_pending"))

    result = TicketStateService(ticket_repository).move("T-012", "review")

    assert result.ticket.status == "review"
    reports = chat_repository.list_messages("default")
    assert len(reports) == 1
    assert reports[0].role == "system_report"
    assert reports[0].report_kind == "done"
    assert reports[0].ticket_id == "T-012"
    assert "in Review" in reports[0].text


def test_intervention_notification_writes_report_and_keeps_webhook(
    tmp_path,
    fresh_ticket_dict,
    monkeypatch,
) -> None:
    connection = connect(tmp_path / "haao.sqlite3")
    ticket_repository = TicketRepository(connection)
    settings_repository = SettingsRepository(connection)
    chat_repository = ChatRepository(connection)
    ticket = ticket_repository.create(_ticket(fresh_ticket_dict, status="blocked"))
    set_notification_webhook(settings_repository, "https://example.test/hook")
    posted: list[dict] = []

    def fake_post(url, *, json, timeout):
        posted.append({"url": url, "json": json, "timeout": timeout})
        return SimpleNamespace()

    monkeypatch.setattr("orchestrator.notifications.httpx.post", fake_post)

    notification = NotificationService(
        ticket_repository,
        settings_repository,
    ).notify_intervention_needed(ticket, "ticket_blocked")

    assert notification.ticket_id == "T-012"
    assert posted[0]["url"] == "https://example.test/hook"
    reports = chat_repository.list_messages("default")
    assert len(reports) == 1
    assert reports[0].report_kind == "blocked"
    assert reports[0].ticket_id == "T-012"


def test_chat_endpoints_shapes(tmp_path) -> None:
    chat_repository = ChatRepository(connect(tmp_path / "haao.sqlite3"))

    class FakeChatService:
        def handle_user_message(self, project_id: str, user_text: str) -> ChatTurnResult:
            segment_id = chat_repository.active_segment_id(project_id)
            user = chat_repository.append_message(
                project_id=project_id,
                role="user",
                text=user_text,
                segment_id=segment_id,
            )
            agent = chat_repository.append_message(
                project_id=project_id,
                role="agent",
                text="Filed one proposal.",
                segment_id=segment_id,
                requirement_id="R-001",
            )
            return ChatTurnResult(messages=[user, agent], filed_requirement_ids=["R-001"])

    app.dependency_overrides[get_chat_repository] = lambda: chat_repository
    app.dependency_overrides[get_chat_service] = lambda: FakeChatService()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/messages",
            json={"project_id": "P-1", "text": "Build chat."},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["filed_requirement_ids"] == ["R-001"]
        assert [message["role"] for message in body["messages"]] == ["user", "agent"]

        after = body["messages"][0]["id"]
        response = client.get(f"/api/chat/messages?project_id=P-1&after={after}")
        assert response.status_code == 200
        assert [message["id"] for message in response.json()["messages"]] == [
            body["messages"][1]["id"]
        ]

        response = client.post(
            "/api/chat/segments",
            json={"project_id": "P-1", "title": "Next"},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is True

        response = client.get("/api/chat/segments?project_id=P-1")
        assert response.status_code == 200
        assert response.json()["segments"][-1]["title"] == "Next"
    finally:
        app.dependency_overrides.clear()
