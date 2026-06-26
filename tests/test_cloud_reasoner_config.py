import types

import pytest
from fastapi.testclient import TestClient

from clients.claude_po import ClaudeTechLeadClient
from clients.openai_compat import OpenAICompatReasoner
from orchestrator.cloud_models import add_cloud_model
from orchestrator.cloud_reasoner_config import (
    build_cloud_reasoner,
    cloud_model_inventory,
    provider_options,
    selected_cloud_reasoner_id,
    validate_cloud_reasoner_id,
)
from orchestrator.config import get_settings
from orchestrator.db.sqlite import SettingsRepository, connect
from orchestrator.main import app


def _settings(**overrides):
    base = dict(
        claude_api_key="",
        openai_api_key="",
        gemini_api_key="",
        claude_model="claude-sonnet-4-6",
        database_url="sqlite:///./haao.sqlite3",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_default_selection_is_anthropic_from_legacy(tmp_path) -> None:
    repo = SettingsRepository(connect(tmp_path / "s.sqlite3"))
    settings = _settings(claude_api_key="k")
    assert selected_cloud_reasoner_id(settings, repo) == "anthropic:claude-sonnet-4-6"
    client = build_cloud_reasoner(settings, repo)
    assert isinstance(client, ClaudeTechLeadClient)
    assert client.model == "claude-sonnet-4-6"


def test_select_openai_builds_openai_compat(tmp_path) -> None:
    repo = SettingsRepository(connect(tmp_path / "s.sqlite3"))
    repo.set_cloud_reasoner("openai:gpt-4o")
    settings = _settings(openai_api_key="ok")
    client = build_cloud_reasoner(settings, repo)
    assert isinstance(client, OpenAICompatReasoner)
    assert client.base_url == "https://api.openai.com/v1"
    assert client.model == "gpt-4o"
    assert client.api_key == "ok"


def test_build_cloud_reasoner_prefers_registry_key(tmp_path, monkeypatch) -> None:
    repo = SettingsRepository(connect(tmp_path / "s.sqlite3"))
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    add_cloud_model(
        repo,
        label="Registered GPT",
        provider="openai",
        model_id="gpt-4o",
        api_key="registry-key",
    )
    repo.set_cloud_reasoner("openai:gpt-4o")
    settings = _settings(openai_api_key="env-key")

    client = build_cloud_reasoner(settings, repo)

    assert isinstance(client, OpenAICompatReasoner)
    assert client.api_key == "registry-key"


def test_validate_cloud_reasoner_id() -> None:
    assert validate_cloud_reasoner_id("openai:gpt-4o") == "openai:gpt-4o"
    # Bare id resolves to the anthropic provider.
    assert validate_cloud_reasoner_id("claude-sonnet-4-6") == "claude-sonnet-4-6"
    with pytest.raises(ValueError, match="Unknown cloud provider"):
        validate_cloud_reasoner_id("acme:something")
    with pytest.raises(ValueError):
        validate_cloud_reasoner_id("openai:")


def test_provider_options_flag_configured_keys() -> None:
    by_id = {o["id"]: o for o in provider_options(_settings(openai_api_key="x"))}
    assert by_id["openai"]["key_configured"] is True
    assert by_id["anthropic"]["key_configured"] is False
    assert by_id["google"]["label"] == "Gemini (Google)"


def test_cloud_model_inventory_includes_non_deletable_default(tmp_path) -> None:
    repo = SettingsRepository(connect(tmp_path / "s.sqlite3"))
    settings = _settings(claude_api_key="ck")

    inventory = cloud_model_inventory(settings, repo)

    assert inventory[0] == {
        "id": "anthropic:claude-sonnet-4-6",
        "label": "Claude (Anthropic) · default",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "key_configured": True,
        "deletable": False,
    }


def test_cloud_reasoner_endpoints(tmp_path, monkeypatch) -> None:
    db = tmp_path / "haao.sqlite3"
    settings = _settings(claude_api_key="ck", openai_api_key="ok", database_url=f"sqlite:///{db}")
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    repo = SettingsRepository(connect(db))
    add_cloud_model(
        repo,
        label="Registered GPT",
        provider="openai",
        model_id="gpt-4o",
        api_key="registry-key",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)

        r = client.get("/config/cloud-reasoner")
        assert r.status_code == 200
        assert r.json()["model_id"] == "anthropic:claude-sonnet-4-6"
        assert any(p["id"] == "openai" and p["key_configured"] for p in r.json()["providers"])
        assert r.json()["registry"][0]["id"] == "anthropic:claude-sonnet-4-6"
        assert r.json()["registry"][0]["deletable"] is False
        assert r.json()["registry"][0]["key_configured"] is True
        assert r.json()["registry"][1]["id"] == "openai:gpt-4o"
        assert r.json()["registry"][1]["key_configured"] is True

        r = client.put("/config/cloud-reasoner", json={"model_id": "openai:gpt-4o"})
        assert r.status_code == 200
        assert r.json()["provider"] == "openai"
        assert r.json()["registry"][1]["id"] == "openai:gpt-4o"

        r = client.get("/config/cloud-reasoner")
        assert r.json()["model_id"] == "openai:gpt-4o"

        r = client.put(
            "/config/cloud-reasoner",
            json={"model_id": "anthropic:claude-sonnet-4-6"},
        )
        assert r.status_code == 200
        assert r.json()["model_id"] == "anthropic:claude-sonnet-4-6"

        r = client.get("/config/cloud-models")
        models = r.json()["models"]
        assert models[0]["id"] == "anthropic:claude-sonnet-4-6"
        assert models[0]["deletable"] is False
        assert models[0]["key_configured"] is True
        assert models[1]["id"] == "openai:gpt-4o"
        assert models[1]["deletable"] is True

        r = client.post(
            "/config/cloud-models",
            json={
                "label": "Default overwrite",
                "provider": "anthropic",
                "model_id": "claude-sonnet-4-6",
                "api_key": "nope",
            },
        )
        assert r.status_code == 400

        r = client.delete("/config/cloud-models/anthropic%3Aclaude-sonnet-4-6")
        assert r.status_code == 400

        r = client.put("/config/cloud-reasoner", json={"model_id": "acme:x"})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
