from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from orchestrator.api import get_pull_request_service
from orchestrator.db.sqlite import IntegrationRepository, RunEventRepository, TicketRepository, connect
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.pr_flow import (
    AcceptanceGateError,
    DirtyWorkspaceError,
    ProviderPullRequestResult,
    PullRequestFlowError,
    PullRequestService,
    pr_branch_name,
)
from tests.conftest import init_git_repo


class FakeProvider:
    name = "github"

    def __init__(self, calls: list[tuple], *, fail_with: str | None = None) -> None:
        self.calls = calls
        self.fail_with = fail_with

    def push_branch(self, branch: str) -> None:
        self.calls.append(("push", branch))

    def open_or_update_pr(
        self,
        *,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
        existing_pr_url: str | None = None,
    ) -> ProviderPullRequestResult:
        self.calls.append(("pr", branch, base_branch, existing_pr_url))
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        status = "updated" if existing_pr_url else "opened"
        return ProviderPullRequestResult(
            pr_url="https://github.com/acme/widgets/pull/7",
            status=status,
        )


def test_pr_flow_enforces_acceptance_gate(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    repo_root, service, calls = _service(tmp_path, fresh_ticket_dict, status="ready")

    with pytest.raises(AcceptanceGateError):
        service.open_or_update_pr("T-777")

    assert calls == []
    assert RunEventRepository(service.repository.connection).list_run_events("default")[0].event_type == "error"
    assert pr_branch_name(service.repository.get("T-777")).startswith("haao/T-777-")
    assert repo_root.exists()


def test_pr_flow_is_idempotent_and_stores_pr_metadata(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    _, service, calls = _service(tmp_path, fresh_ticket_dict)

    first = service.open_or_update_pr("T-777")
    second = service.open_or_update_pr("T-777")

    assert first.status == "opened"
    assert second.status == "updated"
    ticket = service.repository.get("T-777")
    assert ticket.metadata.pr_url == "https://github.com/acme/widgets/pull/7"
    assert ticket.metadata.pr_status == "updated"
    pr_calls = [call for call in calls if call[0] == "pr"]
    assert pr_calls[0][3] is None
    assert pr_calls[1][3] == "https://github.com/acme/widgets/pull/7"


def test_pr_flow_aborts_on_dirty_workspace(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    repo_root, service, calls = _service(tmp_path, fresh_ticket_dict)
    (repo_root / "dirty.txt").write_text("do not overwrite me\n", encoding="utf-8")

    with pytest.raises(DirtyWorkspaceError):
        service.open_or_update_pr("T-777")

    assert calls == []
    event = RunEventRepository(service.repository.connection).list_run_events("default")[0]
    assert event.event_type == "error"
    assert "uncommitted changes" in event.payload["error"]


def test_pr_endpoint_redacts_token_from_response_and_events(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    token = "ghp_secret123456789"
    _, service, _ = _service(
        tmp_path,
        fresh_ticket_dict,
        provider_factory=lambda provider, token, repo_root: FakeProvider(
            [], fail_with=f"provider failed with {token}"
        ),
        token=token,
    )

    app.dependency_overrides[get_pull_request_service] = lambda: service
    try:
        response = TestClient(app).post("/api/tickets/T-777/pr")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert token not in response.text
    events = RunEventRepository(service.repository.connection).list_run_events("default")
    assert token not in str([event.to_dict() for event in events])


def test_pr_success_posts_to_slack_integration(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    _, service, _ = _service(tmp_path, fresh_ticket_dict)
    service.integrations.upsert(
        provider="slack",
        credential_id="main",
        label="Slack",
        token="https://hooks.slack.test/services/secret",
    )
    posts: list[dict] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, json: dict, timeout: float):
        posts.append({"url": url, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("orchestrator.integration_notifications.httpx.post", fake_post)

    service.open_or_update_pr("T-777")

    assert posts
    assert posts[0]["json"]["pr_url"] == "https://github.com/acme/widgets/pull/7"


def _service(
    tmp_path: Path,
    fresh_ticket_dict: dict,
    *,
    status: str = "awaiting_acceptance",
    provider_factory=None,
    token: str = "ghp_secret123456789",
) -> tuple[Path, PullRequestService, list[tuple]]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "calc.py").write_text("def add_one(value):\n    return value + 1\n", encoding="utf-8")
    init_git_repo(repo_root)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/acme/widgets.git"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    base_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    connection = connect(tmp_path / "haao.sqlite3")
    tickets = TicketRepository(connection)
    integrations = IntegrationRepository(connection)
    run_events = RunEventRepository(connection)
    integrations.upsert(
        provider="github",
        credential_id="main",
        label="GitHub",
        token=token,
        scopes=["contents:write", "pull_requests:write"],
    )
    payload = _eligible_ticket(fresh_ticket_dict, status=status)
    payload["metadata"] = {
        "project_id": "default",
        "git_commit": git_commit,
        "git_base_branch": base_branch,
    }
    tickets.create(Ticket.from_dict(payload))
    calls: list[tuple] = []
    factory = provider_factory or (
        lambda provider, token, repo_root: FakeProvider(calls)
    )
    service = PullRequestService(
        repository=tickets,
        integrations=integrations,
        run_events=run_events,
        repo_root=repo_root,
        base_branch=base_branch,
        provider_factory=factory,
    )
    return repo_root, service, calls


def _eligible_ticket(fresh_ticket_dict: dict, *, status: str) -> dict:
    payload = copy.deepcopy(fresh_ticket_dict)
    payload["id"] = "T-777"
    payload["title"] = "Open Product PR"
    payload["status"] = status
    payload["task"]["target_files"] = ["calc.py"]
    payload["context"]["files"] = [{"path": "calc.py", "content": ""}]
    payload["result"] = {
        "outcome": "success",
        "diff": "diff --git a/calc.py b/calc.py",
        "test_output": "passed",
    }
    payload["audit"] = {
        "reviewed_by": "claude-tech-lead",
        "verdict": "approved",
        "feedback": "Looks good",
    }
    return payload
