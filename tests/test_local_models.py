from orchestrator.config import Settings
from orchestrator.db.sqlite import SettingsRepository, connect
from orchestrator.local_models import (
    LocalModelEndpoint,
    cache_local_models,
    default_local_model_endpoint,
    get_local_model_endpoints,
    set_local_model_endpoints,
)
from orchestrator.model_policy import local_execution_model
from orchestrator.role_routing import role_routing_store


def test_default_local_model_endpoint_uses_lmstudio_setting() -> None:
    endpoint = default_local_model_endpoint(
        Settings(LMSTUDIO_BASE_URL="http://localhost:4321/v1")
    )

    assert endpoint.id == "lmstudio"
    assert endpoint.base_url == "http://localhost:4321/v1"


def test_local_model_endpoints_persist_in_settings(tmp_path) -> None:
    repository = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    endpoint = LocalModelEndpoint(
        id="ollama",
        label="Ollama",
        base_url="http://localhost:11434/v1",
        api_key="secret",
    )

    set_local_model_endpoints(repository, [endpoint])

    loaded = get_local_model_endpoints(
        repository,
        Settings(LMSTUDIO_BASE_URL="http://localhost:1234/v1"),
    )
    assert loaded == [endpoint]


def test_local_execution_model_uses_cached_discovered_models(tmp_path) -> None:
    repository = SettingsRepository(connect(tmp_path / "haao.sqlite3"))
    previous_routing = role_routing_store.get()
    try:
        role_routing_store.routing = {**previous_routing, "dev_team": "custom-coder"}
        cache_local_models(repository, ["custom-coder", "custom-gatekeeper"])

        assert local_execution_model("custom-gatekeeper", repository) == "custom-gatekeeper"
        assert local_execution_model("Claude · Tech Lead", repository) == "custom-coder"
        assert local_execution_model("missing-model", repository) == "custom-coder"
    finally:
        role_routing_store.routing = previous_routing
