from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from orchestrator.db.sqlite import TicketRepository, connect
from orchestrator.diff_review import DiffReviewService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_registry import ExecutionRegistry, execution_key, execution_registry
from orchestrator.api import get_git_ticket_flow, get_repository
from orchestrator.main import app
from orchestrator.models.ticket import Ticket
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo
from tests.test_auto_orchestrator import FakeLocalModel, make_ticket


def _make_calc_ticket(fresh_ticket_dict: dict, repo: Path, *, status: str = "ready") -> Ticket:
    ticket = make_ticket(fresh_ticket_dict, status=status)
    ticket_json = ticket.to_dict()
    ticket_json["definition_of_done"]["tests"] = [
        {
            "command": f"{sys.executable} -c \"import calc; assert calc.add_one(1) == 2\"",
            "expect": "pass",
            "timeout_sec": 5,
        }
    ]
    return Ticket.from_dict(ticket_json)


def test_b036_diff_approve_commits_changes_to_ticket_branch(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    ticket = repository.create(_make_calc_ticket(fresh_ticket_dict, workspace_repo))
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        FakeLocalModel("def add_one(value):\n    return value + 1\n"),
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket(ticket.id)
    assert result.ticket.status == "diff_pending"
    assert "return value + 1" not in (workspace_repo / "calc.py").read_text(encoding="utf-8")

    approved = DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    ).approve_diff(ticket.id)

    assert approved.ticket.status == "review"
    assert approved.ticket.audit.verdict == "pending"
    assert "return value + 1" not in (workspace_repo / "calc.py").read_text(encoding="utf-8")
    assert approved.ticket.metadata.git_branch == f"haao/{approved.ticket.id}"
    assert len(approved.ticket.metadata.git_commit) == 40
    from orchestrator.git_flow import GitTicketFlow

    flow = GitTicketFlow(workspace_repo)
    assert flow.current_branch() == approved.ticket.metadata.git_base_branch
    branch_file = flow.run(["git", "show", f"{approved.ticket.metadata.git_branch}:calc.py"]).stdout
    assert "return value + 1" in branch_file


def test_diff_approve_rebuilds_existing_ticket_branch_from_base(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    payload = _make_calc_ticket(fresh_ticket_dict, workspace_repo, status="diff_pending").to_dict()
    payload["result"] = {
        "outcome": "success",
        "diff": (
            "diff --git a/calc.py b/calc.py\n"
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add_one(value):\n"
            "-    return value\n"
            "+    return value + 1\n"
        ),
        "test_output": "ok",
    }
    payload["audit"] = {
        "reviewed_by": "claude-tech-lead",
        "verdict": "rejected",
        "feedback": "old feedback",
    }
    ticket = repository.create(Ticket.from_dict(payload))
    service = DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    )
    first = service.approve_diff(ticket.id).ticket

    from orchestrator.git_flow import GitTicketFlow

    flow = GitTicketFlow(workspace_repo)
    flow.checkout(first.metadata.git_branch)
    payload = first.to_dict()
    payload["status"] = "diff_pending"
    payload["result"] = {
        "outcome": "success",
        "diff": (
            "diff --git a/calc.py b/calc.py\n"
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add_one(value):\n"
            "-    return value\n"
            "+    return value + 2\n"
        ),
        "test_output": "ok",
    }
    repository.save(Ticket.from_dict(payload))

    second = service.approve_diff(ticket.id).ticket

    assert second.status == "review"
    assert second.audit.verdict == "pending"
    assert second.audit.feedback == ""
    assert flow.current_branch() == first.metadata.git_base_branch
    branch_file = flow.run(["git", "show", f"{first.metadata.git_branch}:calc.py"]).stdout
    assert "return value + 2" in branch_file
    assert "return value + 1" not in branch_file


def test_b037_merge_ticket_branch_to_base(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    ticket = repository.create(_make_calc_ticket(fresh_ticket_dict, workspace_repo))
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        FakeLocalModel("def add_one(value):\n    return value + 1\n"),
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )
    loop.run_ticket(ticket.id)
    approved = DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    ).approve_diff(ticket.id)

    from orchestrator.git_flow import GitTicketFlow

    merge = GitTicketFlow(workspace_repo).merge_ticket_branch(approved.ticket)
    payload = approved.ticket.to_dict()
    payload.setdefault("metadata", {})["git_merged_to"] = merge.base_branch
    payload["metadata"]["git_merge_commit"] = merge.merge_commit
    saved = repository.save(Ticket.from_dict(payload))

    assert saved.metadata.git_merged_to == approved.ticket.metadata.git_base_branch
    assert len(saved.metadata.git_merge_commit) == 40


def test_b044_revert_merged_ticket_restores_base_and_reopens_acceptance(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    ticket = repository.create(_make_calc_ticket(fresh_ticket_dict, workspace_repo))
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        FakeLocalModel("def add_one(value):\n    return value + 1\n"),
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )
    loop.run_ticket(ticket.id)
    approved = DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    ).approve_diff(ticket.id)

    from orchestrator.git_flow import GitTicketFlow

    merge = GitTicketFlow(workspace_repo).merge_ticket_branch(approved.ticket)
    payload = approved.ticket.to_dict()
    payload["status"] = "done"
    payload.setdefault("metadata", {})["git_merged_to"] = merge.base_branch
    payload["metadata"]["git_merge_commit"] = merge.merge_commit
    repository.save(Ticket.from_dict(payload))
    assert "return value + 1" in (workspace_repo / "calc.py").read_text(encoding="utf-8")

    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_git_ticket_flow] = lambda: GitTicketFlow(workspace_repo)
    try:
        response = TestClient(app).post(f"/tickets/{ticket.id}/revert")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    updated = response.json()["ticket"]
    assert updated["status"] == "awaiting_acceptance"
    assert len(updated["metadata"]["git_revert_commit"]) == 40
    assert "return value + 1" not in (workspace_repo / "calc.py").read_text(encoding="utf-8")


def test_b036_diff_reject_returns_ticket_to_in_progress(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    payload = _make_calc_ticket(fresh_ticket_dict, workspace_repo, status="diff_pending").to_dict()
    payload["result"] = {
        "outcome": "success",
        "diff": "diff --git a/calc.py b/calc.py\n",
        "test_output": "ok",
    }
    ticket = repository.create(Ticket.from_dict(payload))

    rejected = DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    ).reject_diff(ticket.id, "Please keep the original helper name")

    assert rejected.ticket.status == "in_progress"
    assert rejected.ticket.metadata.diff_rejection_feedback == "Please keep the original helper name"
    assert rejected.ticket.result.outcome == "pending"


def test_b040_cancel_during_execution_resets_ticket_to_ready(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    ticket = repository.create(_make_calc_ticket(fresh_ticket_dict, workspace_repo))

    class SlowModel:
        def chat_completion(self, *, model: str, messages, temperature: float = 0.2) -> str:
            execution_registry.request_cancel(execution_key(repository.project_id, ticket.id))
            return "def add_one(value):\n    return value + 1\n"

    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        SlowModel(),
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket(ticket.id)

    assert result.passed is False
    assert result.ticket.status == "ready"
    assert result.ticket.execution.attempts == 0


def test_b040_cancel_registry_scopes_same_ticket_id_by_project() -> None:
    registry = ExecutionRegistry()
    first_key = execution_key("P-001", "T-001")
    second_key = execution_key("P-002", "T-001")
    registry.register(first_key)
    registry.register(second_key)

    assert registry.request_cancel(first_key) is True
    assert registry.is_cancelled(first_key) is True
    assert registry.is_cancelled(second_key) is False

    registry.unregister(first_key)
    registry.unregister(second_key)
