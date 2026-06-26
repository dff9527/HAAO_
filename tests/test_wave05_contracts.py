from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.cloud_usage import CloudUsage, cost_status_for_provider, usage_from_api_payload
from orchestrator.config import get_settings
from orchestrator.db.sqlite import (
    IntegrationRepository,
    RunEventRepository,
    SettingsRepository,
    TicketRepository,
    connect,
)
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.policies import get_execution_policy, get_retention_policy
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


def _settings(**overrides):
    from types import SimpleNamespace

    base = {
        "claude_api_key": "",
        "openai_api_key": "",
        "gemini_api_key": "",
        "lmstudio_base_url": "http://localhost:1234/v1",
        "local_max_output_tokens": 4096,
        "local_patch_mode_threshold_tokens": 2048,
        "database_url": "sqlite:///./haao.sqlite3",
        "claude_model": "claude-sonnet-4-6",
        "haao_api_token": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_run_events_append_cursor_and_redaction(tmp_path) -> None:
    repo = RunEventRepository(connect(tmp_path / "haao.sqlite3"))

    first = repo.append_run_event(
        project_id="P-1",
        ticket_id="T-001",
        run_id="RUN-1",
        event_type="run_started",
        payload={"note": "sk-secret123456789"},
    )
    second = repo.append_run_event(
        project_id="P-1",
        ticket_id="T-001",
        run_id="RUN-1",
        event_type="egress_attempt",
        payload={"host": "example.com"},
    )

    assert first.id is not None
    assert repo.list_run_events("P-1")[0].payload == {"note": "***redacted***"}
    assert [event.id for event in repo.list_run_events("P-1", after=first.id)] == [second.id]


def test_execution_loop_emits_started_and_finished_run_events(tmp_path, fresh_ticket_dict) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "calc.py").write_text("old", encoding="utf-8")
    init_git_repo(repo_root)
    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    payload = fresh_ticket_dict
    payload["status"] = "ready"
    payload["task"]["target_files"] = ["calc.py"]
    payload["context"]["files"] = [{"path": "calc.py", "content": "old"}]
    payload["definition_of_done"]["tests"] = [
        {"command": "python -c pass", "expect": "pass", "timeout_sec": 120}
    ]
    tickets.create(Ticket.from_dict(payload))

    class Model:
        def chat_completion(self, **kwargs):
            return "new"

    result = ExecutionLoop(
        tickets,
        TicketStateService(tickets),
        Model(),
        repo_root=repo_root,
        test_runner=TestRunner(cwd=repo_root),
    ).run_ticket("T-012")

    assert result.passed is True
    events = RunEventRepository(connection).list_run_events("default")
    assert events[0].event_type == "run_started"
    assert events[-1].event_type == "run_finished"
    assert events[0].ticket_id == "T-012"


def test_policy_defaults(tmp_path) -> None:
    settings = SettingsRepository(connect(tmp_path / "haao.sqlite3"))

    assert get_retention_policy(settings).run_event_retention_days == 90
    execution_policy = get_execution_policy(settings)
    assert execution_policy.test_allow_network is False
    assert "PATH" in execution_policy.env_allowlist


def test_integration_credentials_are_encrypted_and_public_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    repo = IntegrationRepository(connect(tmp_path / "haao.sqlite3"))

    credential = repo.upsert(
        provider="github",
        credential_id="main",
        label="GitHub",
        token="ghp_secret123456789",
        scopes=["contents:write", "pull_requests:write"],
    )

    stored = repo.connection.execute(
        "SELECT encrypted_token FROM integrations WHERE provider = ? AND id = ?",
        ("github", "main"),
    ).fetchone()["encrypted_token"]
    assert "ghp_secret" not in stored
    assert credential.configured is True
    assert repo.decrypted_token("github", "main") == "ghp_secret123456789"


def test_integration_endpoints_do_not_echo_tokens(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    settings = _settings(database_url=f"sqlite:///{tmp_path / 'haao.sqlite3'}")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        response = client.post(
            "/api/config/integrations",
            json={
                "provider": "github",
                "id": "main",
                "label": "GitHub",
                "token": "ghp_secret123456789",
                "scopes": ["contents:write"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert "token" not in body

        listed = client.get("/api/config/integrations").json()["integrations"]
        assert listed[0]["id"] == "main"
        assert "token" not in listed[0]
    finally:
        app.dependency_overrides.clear()


def test_cost_status_semantics() -> None:
    usage = usage_from_api_payload({"usage": {"input_tokens": 10, "output_tokens": 5}})
    assert usage.cost_status == "estimated"
    assert cost_status_for_provider("openai", usage) == "estimated"
    assert cost_status_for_provider("unknown-provider", usage) == "unknown"
    assert cost_status_for_provider("openai", CloudUsage()) == "unknown"
