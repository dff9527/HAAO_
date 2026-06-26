from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import (
    CloudChatReasoner,
    LocalChatReasoner,
    _build_chat_reasoner,
    _parse_reasoner_payload,
    get_settings_repository,
)
from orchestrator.config import get_settings
from orchestrator.db.sqlite import SettingsRepository, connect
from orchestrator.main import app
from orchestrator.model_policy import (
    ALLOW_CLOUD_EXECUTION_SETTINGS_KEY,
    enforce_local_execution_model,
    is_cloud_reasoner_model,
)

_VALID_PAYLOAD = (
    '{"reply":"Got it.","work_items":[{"title":"Add login","prompt":"Implement POST /login"}],'
    '"updated_summary":"User wants login."}'
)


def test_enforce_local_execution_model_default_preserves_local_forcing(tmp_path) -> None:
    repository = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    ticket = {"execution": {"assigned_model": "openai:gpt-4o"}}

    changed = enforce_local_execution_model(ticket, repository)

    assert changed is True
    assigned = ticket["execution"]["assigned_model"]
    assert assigned != "openai:gpt-4o"
    assert not is_cloud_reasoner_model(assigned)


def test_enforce_local_execution_model_opt_in_leaves_cloud_untouched(tmp_path) -> None:
    repository = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    repository.set_json(ALLOW_CLOUD_EXECUTION_SETTINGS_KEY, True)
    ticket = {"execution": {"assigned_model": "openai:gpt-4o"}}

    changed = enforce_local_execution_model(ticket, repository)

    assert changed is False
    assert ticket["execution"]["assigned_model"] == "openai:gpt-4o"


def test_parse_reasoner_payload_plain_json() -> None:
    turn = _parse_reasoner_payload(_VALID_PAYLOAD)

    assert turn.reply == "Got it."
    assert len(turn.work_items) == 1
    assert turn.work_items[0].title == "Add login"
    assert turn.updated_summary == "User wants login."


def test_parse_reasoner_payload_json_code_fence() -> None:
    raw = f"```json\n{_VALID_PAYLOAD}\n```"
    turn = _parse_reasoner_payload(raw)

    assert turn.reply == "Got it."
    assert turn.work_items[0].prompt == "Implement POST /login"


def test_parse_reasoner_payload_prose_wrapped_json() -> None:
    raw = f"Sure — here is the structured response:\n{_VALID_PAYLOAD}\nLet me know if you need more."
    turn = _parse_reasoner_payload(raw)

    assert turn.reply == "Got it."
    assert turn.work_items[0].title == "Add login"


def test_parse_reasoner_payload_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid JSON"):
        _parse_reasoner_payload("This is not JSON at all.")


def test_parse_reasoner_payload_rejects_empty_work_item_title() -> None:
    with pytest.raises(ValueError, match="title cannot be empty"):
        _parse_reasoner_payload(
            '{"reply":"ok","work_items":[{"title":"   ","prompt":"do work"}]}',
        )


def test_chat_reasoner_config_default_cloud_and_persists_local(tmp_path) -> None:
    settings_repository = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    app.dependency_overrides[get_settings_repository] = lambda: settings_repository
    try:
        client = TestClient(app)

        assert client.get("/config/chat-reasoner").json() == {"mode": "cloud"}

        response = client.put("/config/chat-reasoner", json={"mode": "local"})
        assert response.status_code == 200
        assert response.json() == {"mode": "local"}
        assert client.get("/config/chat-reasoner").json() == {"mode": "local"}
    finally:
        app.dependency_overrides.clear()


def test_build_chat_reasoner_local_mode_without_local_model_errors_not_cloud(
    tmp_path,
    monkeypatch,
) -> None:
    settings_repository = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    settings_repository.set_json("chat_reasoner_mode", "local")
    monkeypatch.setattr("orchestrator.api._local_chat_model", lambda _repo: None)
    tech_lead = MagicMock()
    ticket_repository = MagicMock()

    reasoner = _build_chat_reasoner(
        tech_lead=tech_lead,
        ticket_repository=ticket_repository,
        settings=get_settings(),
        settings_repository=settings_repository,
    )

    # Local stays local — a privacy/cost choice is never silently routed to cloud.
    assert isinstance(reasoner, LocalChatReasoner)
    assert not isinstance(reasoner, CloudChatReasoner)
    # Sending surfaces a clear error instead of falling back to a cloud model.
    with pytest.raises(ValueError):
        reasoner.respond(summary="", recent=[], user_text="hi")
