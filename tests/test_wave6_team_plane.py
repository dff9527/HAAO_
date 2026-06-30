from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.cloud_models import add_cloud_model
from orchestrator.config import get_settings
from orchestrator.db.sqlite import (
    AuditRepository,
    IdentityRepository,
    IntegrationRepository,
    RunEventRepository,
    SettingsRepository,
    TicketRepository,
    connect,
)
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.pr_flow import ProviderPullRequestResult, PullRequestService
from tests.conftest import init_git_repo


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


class FakeProvider:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def push_branch(self, branch: str) -> None:
        self.calls.append(("push", branch))

    def open_or_update_pr(self, *, branch, base_branch, title, body, existing_pr_url=None):
        self.calls.append(("pr", branch, base_branch, existing_pr_url))
        return ProviderPullRequestResult(
            pr_url="https://github.com/acme/widgets/pull/42",
            status="opened",
        )


class FakeAppMinter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def mint_installation_token(self, provider, app_payload):
        self.calls.append((provider, app_payload))
        return "installation-token-123"


def test_rbac_matrix_for_privileged_action(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    connection = connect(db_path)
    identity = IdentityRepository(connection)
    for role in ("owner", "admin", "member", "viewer"):
        identity.set_membership(user_id=f"user-{role}", workspace_id="default", role=role)

    client = TestClient(app)
    statuses = {}
    for role in ("owner", "admin", "member", "viewer"):
        response = client.post(
            "/api/config/integrations",
            headers=_headers(f"user-{role}"),
            json={
                "provider": "github",
                "token": f"token-{role}",
                "scopes": ["repo"],
                "label": role,
                "id": role,
            },
        )
        statuses[role] = response.status_code
        if response.status_code == 403:
            assert response.json()["reason"] == "forbidden"

    assert statuses == {"owner": 200, "admin": 200, "member": 403, "viewer": 403}


def test_auth_off_uses_implicit_single_owner(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("HAAO_API_TOKEN", raising=False)
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    try:
        response = TestClient(app).post(
            "/api/config/integrations",
            json={"provider": "github", "token": "pat", "scopes": ["repo"], "label": "GitHub"},
        )
    finally:
        get_settings.cache_clear()
    assert response.status_code == 200


def test_audit_events_are_paged_and_redacted(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    connection = connect(db_path)
    identity = IdentityRepository(connection)
    identity.set_membership(user_id="admin", workspace_id="default", role="admin")
    add_cloud_model(
        SettingsRepository(connection),
        provider="openai",
        model_id="gpt-4o",
        api_key="secret-model-key",
    )
    audit = AuditRepository(connection)
    first = audit.append(
        actor_id="admin",
        workspace_id="default",
        action="key.rotate",
        target="cloud-model",
        payload={"token": "secret-model-key"},
    )
    audit.append(
        actor_id="admin",
        workspace_id="default",
        action="model.change",
        target="dev",
    )

    client = TestClient(app)
    page1 = client.get("/api/audit?workspace=default&limit=1", headers=_headers("admin"))
    page2 = client.get(f"/api/audit?workspace=default&cursor={first.id}&limit=10", headers=_headers("admin"))

    assert page1.status_code == 200
    assert page1.json()["events"][0]["payload"]["token"] == "***redacted***"
    assert page2.status_code == 200
    assert [event["action"] for event in page2.json()["events"]] == ["model.change"]


def test_runner_protocol_round_trip_and_revocation(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    IdentityRepository(connect(db_path)).set_membership(user_id="admin", workspace_id="default", role="admin")
    client = TestClient(app)

    registered = client.post(
        "/api/runner/register",
        headers=_headers("admin"),
        json={"workspace_id": "default", "label": "local"},
    )
    assert registered.status_code == 200
    token = registered.json()["token"]
    runner_id = registered.json()["runner"]["id"]

    job = client.post(
        "/api/runner/jobs",
        headers=_headers("admin"),
        json={"workspace_id": "default", "ticket_id": "T-001", "payload": {"ticket_id": "T-001"}},
    )
    assert job.status_code == 200

    runner_headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/runner/heartbeat", headers=runner_headers).status_code == 200
    leased = client.post("/api/runner/lease", headers=runner_headers, json={"ttl_sec": 60})
    assert leased.status_code == 200
    job_id = leased.json()["job"]["id"]
    assert leased.json()["job"]["status"] == "leased"

    streamed = client.post(
        "/api/runner/events",
        headers=runner_headers,
        json={
            "job_id": job_id,
            "events": [
                {
                    "project_id": "default",
                    "ticket_id": "T-001",
                    "run_id": "RUN-1",
                    "event_type": "report",
                    "payload": {"stage": "runner"},
                }
            ]
        },
    )
    assert streamed.status_code == 200
    assert streamed.json()["accepted"] == 1
    assert RunEventRepository(connect(db_path)).list_run_events("default", ticket_id="T-001")[0].payload["stage"] == "runner"

    completed = client.post(
        f"/api/runner/jobs/{job_id}/complete",
        headers=runner_headers,
        json={"status": "terminal", "result": {"outcome": "success"}},
    )
    assert completed.status_code == 200
    assert completed.json()["job"]["status"] == "terminal"

    revoked = client.post(f"/api/runner/revoke/{runner_id}", headers=_headers("admin"))
    assert revoked.status_code == 200
    assert client.post("/api/runner/heartbeat", headers=runner_headers).status_code == 401


def test_pr_flow_uses_app_credential_with_mocked_installation_token(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("value = 1\n", encoding="utf-8")
    init_git_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/acme/widgets.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    connection = connect(tmp_path / "haao.sqlite3")
    repository = TicketRepository(connection)
    ticket = _pr_ticket(fresh_ticket_dict, head)
    repository.create(ticket)
    integrations = IntegrationRepository(connection)
    integrations.upsert(
        provider="github",
        credential_id="github-app",
        label="GitHub App",
        scopes=["credential:app"],
        token=json.dumps({"installation_id": "123", "workspace_id": "default"}),
    )
    calls: list[tuple] = []
    minter = FakeAppMinter()
    service = PullRequestService(
        repository=repository,
        integrations=integrations,
        run_events=RunEventRepository(connection),
        repo_root=repo,
        provider_factory=lambda provider, token, repo_root: FakeProvider(calls),
        app_token_minter=minter,
    )

    result = service.open_or_update_pr("T-777")

    assert result.pr_url == "https://github.com/acme/widgets/pull/42"
    assert minter.calls == [("github", {"installation_id": "123", "workspace_id": "default"})]
    assert calls[0] == ("push", "haao/T-777-ship-ticket")
    audit_actions = [event.action for event in AuditRepository(connection).list(workspace_id="default")]
    assert "git.app_token.mint" in audit_actions
    assert "git.pr.open" in audit_actions


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


def _pr_ticket(base: dict, head: str) -> Ticket:
    payload = copy.deepcopy(base)
    payload["id"] = "T-777"
    payload["title"] = "Ship ticket"
    payload["status"] = "awaiting_acceptance"
    payload["task"]["target_files"] = ["calc.py"]
    payload["context"]["files"] = [{"path": "calc.py", "content": "value = 1\n"}]
    payload["result"] = {
        "outcome": "success",
        "diff": "diff --git a/calc.py b/calc.py\n",
        "test_output": "ok",
    }
    payload["audit"] = {"verdict": "approved", "feedback": "ok", "reviewed_by": "gatekeeper"}
    payload["metadata"] = {
        "project_id": "default",
        "accepted_at": "2026-06-29T00:00:00+00:00",
        "git_commit": head,
        "git_base_branch": "master",
    }
    return Ticket.from_dict(payload)
