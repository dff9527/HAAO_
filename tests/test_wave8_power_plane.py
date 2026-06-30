from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from orchestrator.app_tokens import RealAppTokenMinter
from orchestrator.config import Settings, get_settings
from orchestrator.db.sqlite import (
    AuditRepository,
    GitAppInstallationRepository,
    IdentityRepository,
    IntegrationRepository,
    RunEventRepository,
    RunnerRepository,
    connect,
)
from orchestrator.main import app
from orchestrator.pr_flow import AppGitCredential
from orchestrator.runner_client.config import RunnerClientConfig
from orchestrator.runner_client.daemon import RunnerDaemon
from orchestrator.runner_client.state import RunnerState, RunnerStateStore
from orchestrator.runner_client.transport import RunnerTransport


def teardown_function() -> None:
    get_settings.cache_clear()


def test_client_runner_round_trip_against_in_process_control_plane(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    client = TestClient(app)
    admin_headers = _headers("admin")
    queued = client.post(
        "/api/runner/jobs",
        headers=admin_headers,
        json={"workspace_id": "default", "ticket_id": "T-888", "payload": {"ticket_id": "T-888"}},
    )
    assert queued.status_code == 200

    class FakeExecutor:
        def execute(self, job):
            assert job["ticket_id"] == "T-888"
            return [
                {
                    "ticket_id": "T-888",
                    "run_id": "RUN-wave8",
                    "event_type": "report",
                    "payload": {"stage": "runner-client"},
                }
            ], {"outcome": "success", "ticket_id": "T-888"}

    daemon = RunnerDaemon(
        RunnerClientConfig(
            control_plane_url="http://testserver",
            workspace_id="default",
            label="wave8-test",
            repo_root=tmp_path,
            state_path=tmp_path / "runner-state.json",
            api_token="team-token",
        ),
        transport=RunnerTransport(
            "http://testserver",
            api_token="team-token",
            http_client=client,
            backoff_base_sec=0,
        ),
        executor=FakeExecutor(),
    )

    assert daemon.run_once() is True
    events = RunEventRepository(connect(db_path)).list_run_events("default", ticket_id="T-888")
    assert events[0].payload["stage"] == "runner-client"
    assert RunnerRepository(connect(db_path)).get_job(queued.json()["job"]["id"]).status == "terminal"


def test_runner_lease_expiry_reclaim_release_and_revoke(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    connection = connect(db_path)
    IdentityRepository(connection).set_membership(user_id="admin", workspace_id="default", role="admin")
    repository = RunnerRepository(connection)
    first = repository.issue_token(workspace_id="default", label="first")
    second = repository.issue_token(workspace_id="default", label="second")
    job = repository.enqueue_job(workspace_id="default", ticket_id="T-001", payload={})

    leased = repository.lease_next_job(runner=first.runner, ttl_sec=300)
    assert leased.id == job.id
    connection.execute(
        "UPDATE runner_jobs SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job.id),
    )
    connection.commit()
    reclaimed = repository.lease_next_job(runner=second.runner, ttl_sec=300)
    assert reclaimed.id == job.id
    assert reclaimed.lease_runner_id == second.runner.id
    old_expiry = reclaimed.lease_expires_at
    repository.heartbeat(second.token, lease_ttl_sec=600)
    renewed = repository.get_job(job.id)
    assert renewed.lease_expires_at > old_expiry

    released = repository.release_job(job_id=job.id, runner=second.runner)
    assert released.status == "queued"
    repository.revoke(second.runner.id)
    assert repository.heartbeat(second.token) is None


def test_runner_stops_on_401_and_does_not_log_tokens_or_keys(tmp_path: Path, caplog, monkeypatch) -> None:
    token = "hrun_super_secret_runner_token"
    provider_key = "sk-provider-secret"
    monkeypatch.setenv("OPENAI_API_KEY", provider_key)

    class UnauthorizedTransport:
        def register(self, **kwargs):
            return {"runner": {"id": "runner-1"}, "token": token}

        def heartbeat(self, runner_token):
            from orchestrator.runner_client.transport import RunnerUnauthorized

            raise RunnerUnauthorized("nope")

        def release(self, runner_token, *, job_id):
            raise AssertionError("no lease should be released")

    state = RunnerStateStore(tmp_path / "state.json")
    state.save(RunnerState(runner_id="runner-1", token=token))
    daemon = RunnerDaemon(
        RunnerClientConfig(
            control_plane_url="http://control.test",
            state_path=tmp_path / "state.json",
            repo_root=tmp_path,
            max_idle_cycles=1,
        ),
        transport=UnauthorizedTransport(),
        state_store=state,
        executor=lambda job: ([], {}),
    )

    caplog.set_level(logging.INFO)
    with caplog.at_level(logging.INFO):
        daemon.run_forever()

    assert not state.load().registered
    assert token not in caplog.text
    assert provider_key not in caplog.text
    assert "Runner token revoked" in caplog.text


def test_github_app_minter_exchanges_caches_and_remints(monkeypatch) -> None:
    private_key = _private_key_pem()
    now = [datetime(2026, 6, 29, tzinfo=UTC)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/app/installations/123/access_tokens"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(
            201,
            json={
                "token": f"github-installation-token-{len(calls)}",
                "expires_at": (now[0] + timedelta(seconds=120)).isoformat(),
            },
        )

    monkeypatch.setenv("GITHUB_APP_ID", "42")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", private_key)
    settings = Settings()
    minter = RealAppTokenMinter(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now[0],
    )

    first = minter.mint_installation_token("github", {"installation_id": "123"})
    second = minter.mint_installation_token("github", {"installation_id": "123"})
    now[0] = now[0] + timedelta(seconds=70)
    third = minter.mint_installation_token("github", {"installation_id": "123"})

    assert first == second == "github-installation-token-1"
    assert third == "github-installation-token-2"
    assert len(calls) == 2


def test_gitlab_app_minter_uses_mock_exchange_and_cache(monkeypatch) -> None:
    now = [datetime(2026, 6, 29, tzinfo=UTC)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/api/v4/projects/group/project/access_tokens"
        assert request.headers["private-token"] == "bootstrap-secret"
        return httpx.Response(201, json={"token": "gitlab-project-token"})

    monkeypatch.setenv("GITLAB_APP_BOOTSTRAP_TOKEN", "bootstrap-secret")
    settings = Settings()
    minter = RealAppTokenMinter(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now[0],
    )

    first = minter.mint_installation_token(
        "gitlab",
        {"installation_id": "group/project", "target_type": "project"},
    )
    second = minter.mint_installation_token(
        "gitlab",
        {"installation_id": "group/project", "target_type": "project"},
    )

    assert first == second == "gitlab-project-token"
    assert len(calls) == 1


def test_installation_storage_round_trip_and_pr_flow_audits_mint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GITHUB_APP_ID", "42")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", _private_key_pem())
    connection = connect(tmp_path / "haao.sqlite3")
    installs = GitAppInstallationRepository(connection)
    stored = installs.upsert(
        workspace_id="default",
        provider="github",
        account="acme",
        installation_id="123",
        payload={"permissions": {"contents": "write"}},
    )
    assert installs.get(workspace_id="default", provider="github", account="acme") == stored
    assert installs.revoke(workspace_id="default", provider="github", account="acme").revoked_at

    installs.upsert(
        workspace_id="default",
        provider="github",
        account="acme",
        installation_id="123",
        payload={},
    )
    integrations = IntegrationRepository(connection)
    credential = integrations.upsert(
        provider="github",
        credential_id="github-app",
        label="GitHub App",
        scopes=["credential:app"],
        token='{"workspace_id":"default","account":"acme"}',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "token": "installation-token",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            },
        )

    resolved = AppGitCredential(
        credential,
        integrations,
        RealAppTokenMinter(
            Settings(),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        AuditRepository(connection),
        installs,
    ).resolve_token()

    assert resolved.token == "installation-token"
    actions = [event.action for event in AuditRepository(connection).list(workspace_id="default")]
    assert "git.app_token.mint" in actions


def _configure_env(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HAAO_API_TOKEN", "team-token")
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    get_settings.cache_clear()


def _headers(user_id: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer team-token",
        "X-HAAO-User-Id": user_id,
        "X-HAAO-Workspace-Id": "default",
    }


def _private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
