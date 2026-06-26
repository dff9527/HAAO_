from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

from clients.claude_po import AuditResult
from clients.lmstudio import ChatMessage
from orchestrator.cloud_usage import CloudUsage
from orchestrator.db.sqlite import RequirementRepository, TicketRepository, connect
from orchestrator.execution_loop import ExecutionLoop, build_file_rewrite_prompt
from orchestrator.models.requirement import Requirement
from orchestrator.models.ticket import Ticket
from orchestrator.policies import ExecutionPolicy
from orchestrator.requirements_flow import RequirementService
from orchestrator.review_flow import ReviewService
from orchestrator.role_routing import role_routing_store
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


class FakeDecomposer:
    def __init__(self, ticket_payload: dict) -> None:
        self.ticket_payload = ticket_payload

    def decompose(
        self,
        requirement: str,
        repo_context: str,
        *,
        scope_paths: list[str] | None = None,
        constraints: list[str] | None = None,
        acceptance_notes: str = "",
        attachments: list[dict] | None = None,
    ) -> list[dict]:
        assert "add_one" in requirement
        assert "Repository summary:" in repo_context
        assert "calc.py" in repo_context
        assert scope_paths == ["calc.py"]
        assert constraints == ["Only touch calc.py"]
        assert acceptance_notes == "PO expects visible increment behavior"
        return [copy.deepcopy(self.ticket_payload)]


class FakeLocalModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        self.prompts.append(messages[0].content if isinstance(messages[0], ChatMessage) else messages[0]["content"])
        return self.output


class FakeAuditor:
    def __init__(self, result: AuditResult) -> None:
        self.result = result

    def audit(self, ticket: Ticket | dict, diff: str) -> AuditResult:
        assert diff
        return self.result


class BrokenAuditor:
    def audit(self, ticket: Ticket | dict, diff: str) -> AuditResult:
        raise RuntimeError("Claude response contained invalid JSON")


class UsageAuditor(FakeAuditor):
    last_usage = CloudUsage(input_tokens=100, output_tokens=20)


def make_calc_ticket(ticket_dict: dict, repo: Path, *, status: str = "backlog") -> dict:
    payload = copy.deepcopy(ticket_dict)
    payload["id"] = "T-200"
    payload["title"] = "Fix add_one"
    payload["type"] = "bugfix"
    payload["status"] = status
    payload["dependencies"] = []
    payload["task"] = {
        "description": "Change add_one so it returns value + 1.",
        "target_files": ["calc.py"],
        "constraints": ["Only edit calc.py"],
    }
    payload["context"] = {"files": []}
    payload["definition_of_done"] = {
        "tests": [
            {
                "command": f"{sys.executable} -c \"import calc; assert calc.add_one(1) == 2\"",
                "expect": "pass",
                "timeout_sec": 5,
            }
        ],
        "acceptance_criteria": ["add_one increments the input"],
    }
    payload["execution"] = {
        "assigned_model": "qwen3-coder-next",
        "retry_budget": 1,
        "attempts": 0,
        "escalate_to": "tech_lead",
    }
    payload["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
    payload.pop("result", None)
    return payload


def test_b038_execution_loop_preserves_project_runner_settings(
    calc_repo: Path,
    calc_db_path: Path,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    state_service = TicketStateService(repository)
    runner = TestRunner(
        cwd=calc_repo,
        env={"HAAO_FLAG": "ok"},
        execution_policy=ExecutionPolicy(
            test_allow_network=False,
            env_allowlist=("PATH", "PYTHONPATH", "HAAO_FLAG"),
            sandbox_mode="none",
        ),
        setup_cmd="python -c \"print('setup')\"",
        cleanup_cmd="python -c \"print('cleanup')\"",
        setup_timeout_sec=7,
        cleanup_timeout_sec=8,
    )
    loop = ExecutionLoop(
        repository,
        state_service,
        FakeLocalModel(""),
        repo_root=calc_repo,
        test_runner=runner,
    )

    worktree_runner = loop._test_runner_for(calc_repo / ".haao-worktree")

    assert worktree_runner.cwd == (calc_repo / ".haao-worktree").resolve()
    assert worktree_runner.env == {"HAAO_FLAG": "ok"}
    assert worktree_runner.setup_cmd == runner.setup_cmd
    assert worktree_runner.cleanup_cmd == runner.cleanup_cmd
    assert worktree_runner.setup_timeout_sec == 7
    assert worktree_runner.cleanup_timeout_sec == 8
    assert worktree_runner.execution_policy == runner.execution_policy


@pytest.fixture
def calc_repo(workspace_repo: Path) -> Path:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    return workspace_repo


@pytest.fixture
def calc_db_path(calc_repo: Path) -> Path:
    return calc_repo.with_suffix(".sqlite3")


def test_b009_requirement_flow_decomposes_injects_and_persists(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    requirement_repository = RequirementRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo)
    service = RequirementService(
        repository,
        requirement_repository,
        FakeDecomposer(payload),
        repo_root=calc_repo,
    )

    result = service.decompose_preview(
        Requirement(
            id="R-001",
            prompt="Please fix add_one",
            scope_paths=["calc.py"],
            constraints=["Only touch calc.py"],
            acceptance_notes="PO expects visible increment behavior",
        )
    )

    assert len(result.proposed_tickets) == 1
    assert repository.list() == []
    stored_requirement = requirement_repository.get("R-001")
    assert stored_requirement.status == "preview_ready"
    assert stored_requirement.proposed_tickets[0]["metadata"]["requirement_id"] == "R-001"

    confirmed = service.confirm(
        "R-001",
        [ticket.to_dict() for ticket in result.proposed_tickets],
    )

    assert confirmed.requirement.status == "confirmed"
    assert confirmed.requirement.generated_ticket_ids == ["T-001"]
    stored = repository.get("T-001")
    assert stored.status == "ready"
    assert stored.metadata.needs_approval is False
    assert stored.metadata.requirement_id == "R-001"
    assert stored.metadata.source_ticket_id == "T-200"
    assert stored.context.files[0].path == "calc.py"
    assert "return value" in stored.context.files[0].content
    assert "Only touch calc.py" in stored.task.constraints
    assert "PO expects visible increment behavior" in stored.definition_of_done.acceptance_criteria


def test_b017_confirm_renumbers_ticket_ids_and_dependencies_globally(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    requirement_repository = RequirementRepository(connect(calc_db_path))
    service = RequirementService(
        repository,
        requirement_repository,
        FakeDecomposer(make_calc_ticket(fresh_ticket_dict, calc_repo)),
        repo_root=calc_repo,
    )

    first_payload = make_calc_ticket(fresh_ticket_dict, calc_repo)
    first_payload["id"] = "T-001"
    first_payload["title"] = "First generated ticket"
    second_payload = make_calc_ticket(fresh_ticket_dict, calc_repo)
    second_payload["id"] = "T-002"
    second_payload["title"] = "Depends on first generated ticket"
    second_payload["dependencies"] = ["T-001"]

    for requirement_id in ("R-001", "R-002"):
        requirement_repository.create(
            Requirement(
                id=requirement_id,
                prompt="Please fix add_one",
                status="preview_ready",
                proposed_tickets=[first_payload, second_payload],
            )
        )

    first = service.confirm("R-001")
    second = service.confirm("R-002")

    assert first.requirement.generated_ticket_ids == ["T-001", "T-002"]
    assert second.requirement.generated_ticket_ids == ["T-003", "T-004"]
    assert repository.get("T-002").dependencies == ["T-001"]
    assert repository.get("T-004").dependencies == ["T-003"]
    assert {ticket.id for ticket in repository.list()} == {"T-001", "T-002", "T-003", "T-004"}


def test_b019_requirement_flow_forces_execution_model_to_local(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    previous_routing = role_routing_store.get()
    previous_settings_repository = role_routing_store._settings_repository
    role_routing_store._settings_repository = None
    role_routing_store.routing = {**previous_routing, "dev_team": "qwen3-coder-next"}
    try:
        repository = TicketRepository(connect(calc_db_path))
        requirement_repository = RequirementRepository(connect(calc_db_path))
        payload = make_calc_ticket(fresh_ticket_dict, calc_repo)
        payload["execution"]["assigned_model"] = "claude-tech-lead"
        service = RequirementService(
            repository,
            requirement_repository,
            FakeDecomposer(payload),
            repo_root=calc_repo,
        )

        preview = service.decompose_preview(
            Requirement(
                id="R-001",
                prompt="Please fix add_one",
                scope_paths=["calc.py"],
                constraints=["Only touch calc.py"],
                acceptance_notes="PO expects visible increment behavior",
            )
        )
        confirmed = service.confirm(
            "R-001",
            [ticket.to_dict() for ticket in preview.proposed_tickets],
        )

        assert preview.proposed_tickets[0].execution.assigned_model == "qwen3-coder-next"
        assert confirmed.tickets[0].execution.assigned_model == "qwen3-coder-next"
    finally:
        role_routing_store.routing = previous_routing
        role_routing_store._settings_repository = previous_settings_repository


def test_b013_requirement_scope_rejects_path_traversal(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    service = RequirementService(
        TicketRepository(connect(calc_db_path)),
        RequirementRepository(connect(calc_db_path)),
        FakeDecomposer(make_calc_ticket(fresh_ticket_dict, calc_repo)),
        repo_root=calc_repo,
    )

    with pytest.raises(ValueError, match="cannot contain"):
        service.decompose_preview(
            Requirement(
                id="R-001",
                prompt="Please fix add_one",
                scope_paths=["../secrets.txt"],
            )
        )


def test_b010_execution_loop_applies_diff_runs_tests_and_moves_to_review(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    ticket = Ticket.from_dict(make_calc_ticket(fresh_ticket_dict, calc_repo, status="ready"))
    ticket = repository.create(ticket)
    model = FakeLocalModel("def add_one(value):\n    return value + 1\n")
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=calc_repo,
        test_runner=TestRunner(cwd=calc_repo),
    )

    result = loop.run_ticket(ticket.id)

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    persisted = repository.get(ticket.id)
    assert persisted.result.outcome == "success"
    assert "diff --git a/calc.py b/calc.py" in persisted.result.diff
    assert "return value + 1" in persisted.result.diff
    assert "return value + 1" not in (calc_repo / "calc.py").read_text(encoding="utf-8")
    assert "complete updated file content" in model.prompts[0]


def test_execution_loop_feeds_each_rewrite_to_the_next_target_prompt(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    test_file = calc_repo / "test_calc.py"
    test_file.write_text("import calc\nassert calc.add_one(1) == 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "test_calc.py"], cwd=calc_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add test target"],
        cwd=calc_repo,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            **os.environ,
        },
    )
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="ready")
    payload["task"]["target_files"] = ["calc.py", "test_calc.py"]
    payload["context"] = {
        "files": [
            {"path": "calc.py", "content": "def add_one(value):\n    return value\n"},
            {"path": "test_calc.py", "content": test_file.read_text(encoding="utf-8")},
        ]
    }
    payload["definition_of_done"]["tests"][0]["command"] = (
        f"{sys.executable} test_calc.py"
    )
    ticket = repository.create(Ticket.from_dict(payload))

    class CoordinatedModel:
        prompts: list[str]

        def __init__(self) -> None:
            self.prompts = []

        def chat_completion(self, *, model: str, messages, temperature: float = 0.2) -> str:
            prompt = messages[0].content
            self.prompts.append(prompt)
            if "Requested target file:\ncalc.py" in prompt:
                return "def add_one(value):\n    return value + 1\n"
            return "import calc\nassert calc.add_one(1) == 2\n"

    model = CoordinatedModel()
    result = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=calc_repo,
        test_runner=TestRunner(cwd=calc_repo),
    ).run_ticket(ticket.id)

    assert result.passed is True
    assert len(model.prompts) == 2
    assert "File: calc.py\n```\ndef add_one(value):\n    return value + 1" in model.prompts[1]


def test_b016_execution_loop_rejects_target_file_outside_repo(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="ready")
    payload["task"]["target_files"] = ["../other.py"]
    payload["context"]["files"] = []
    payload["execution"]["retry_budget"] = 0
    ticket = Ticket.from_dict(payload)
    ticket = repository.create(ticket)
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        FakeLocalModel("x = 1\n"),
        repo_root=calc_repo,
        test_runner=TestRunner(cwd=calc_repo),
    )

    result = loop.run_ticket(ticket.id)

    assert result.passed is False
    assert result.escalated is True
    assert result.ticket.status == "blocked"
    assert result.ticket.execution.attempts == 1
    assert "resolves outside repo_root" in result.ticket.result.test_output
    assert (calc_repo / "other.py").exists() is False


class ObservingLocalModel:
    def __init__(self, output: str, repo: Path) -> None:
        self.output = output
        self.repo = repo
        self.observed_contents: list[str] = []
        self.prompts: list[str] = []

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        self.observed_contents.append((self.repo / "calc.py").read_text(encoding="utf-8"))
        self.prompts.append(messages[0].content if isinstance(messages[0], ChatMessage) else messages[0]["content"])
        return self.output


def test_b010_execution_loop_rolls_back_workspace_before_retry(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="ready")
    payload["execution"]["retry_budget"] = 2
    payload["definition_of_done"]["tests"][0]["command"] = (
        f"{sys.executable} -c \"import sys; sys.exit(5)\""
    )
    ticket = repository.create(Ticket.from_dict(payload))
    model = ObservingLocalModel("def add_one(value):\n    return value + 1\n", calc_repo)
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=calc_repo,
        test_runner=TestRunner(cwd=calc_repo),
    )

    loop.run_ticket(ticket.id)

    assert len(model.observed_contents) >= 2
    assert all("return value + 1" not in content for content in model.observed_contents)
    baseline_context = (
        "File: calc.py\n```\ndef add_one(value):\n    return value\n\n```"
    )
    assert all(baseline_context in prompt for prompt in model.prompts)


def test_b011_review_approved_moves_to_awaiting_acceptance(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="review")
    payload["result"] = {"outcome": "success", "diff": "diff --git a/calc.py b/calc.py", "test_output": "passed"}
    repository.create(Ticket.from_dict(payload))
    service = ReviewService(
        repository,
        TicketStateService(repository),
        FakeAuditor(AuditResult(verdict="approved", feedback="Looks good")),
    )

    result = service.review_ticket("T-200")

    assert result.ticket.status == "awaiting_acceptance"
    assert result.ticket.audit.verdict == "approved"
    assert result.ticket.audit.reviewed_by == "claude-tech-lead"
    assert repository.logs_for_ticket("T-200")[-1]["message"].startswith("Technical audit approved")


def test_review_approved_records_cloud_usage_with_metadata_model(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="review")
    payload["metadata"] = {"requirement_id": "R-001"}
    payload["result"] = {"outcome": "success", "diff": "diff --git a/calc.py b/calc.py", "test_output": "passed"}
    repository.create(Ticket.from_dict(payload))
    service = ReviewService(
        repository,
        TicketStateService(repository),
        UsageAuditor(AuditResult(verdict="approved", feedback="Looks good")),
    )

    result = service.review_ticket("T-200")

    assert result.ticket.status == "awaiting_acceptance"
    stored = repository.get("T-200")
    assert stored.metadata.cloud_input_tokens == 100
    assert stored.metadata.cloud_output_tokens == 20


def test_b011_review_rejected_returns_to_backlog(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="review")
    payload["result"] = {"outcome": "success", "diff": "diff --git a/calc.py b/calc.py", "test_output": "passed"}
    repository.create(Ticket.from_dict(payload))
    service = ReviewService(
        repository,
        TicketStateService(repository),
        FakeAuditor(AuditResult(verdict="rejected", feedback="Add edge case")),
    )

    result = service.review_ticket("T-200")

    assert result.ticket.status == "backlog"
    assert result.ticket.audit.verdict == "rejected"
    assert result.ticket.audit.feedback == "Add edge case"


def test_review_audit_error_is_recorded_on_ticket(
    calc_repo: Path,
    calc_db_path: Path,
    fresh_ticket_dict: dict,
) -> None:
    repository = TicketRepository(connect(calc_db_path))
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="review")
    payload["result"] = {"outcome": "success", "diff": "diff --git a/calc.py b/calc.py", "test_output": "passed"}
    repository.create(Ticket.from_dict(payload))
    service = ReviewService(
        repository,
        TicketStateService(repository),
        BrokenAuditor(),
    )

    with pytest.raises(RuntimeError, match="invalid JSON"):
        service.review_ticket("T-200")

    stored = repository.get("T-200")
    assert stored.status == "review"
    assert stored.audit.verdict == "pending"
    assert "Technical audit failed" in stored.audit.feedback
    assert repository.logs_for_ticket("T-200")[-1]["level"] == "error"


def test_rework_prompt_uses_preserved_previous_rejected_diff(
    calc_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    payload = make_calc_ticket(fresh_ticket_dict, calc_repo, status="ready")
    payload["audit"] = {
        "reviewed_by": "claude-tech-lead",
        "verdict": "rejected",
        "feedback": "Fix the edge case.",
    }
    payload["result"] = {"outcome": "pending"}
    payload["metadata"] = {
        "previous_rejected_diff": "diff --git a/calc.py b/calc.py\n+bad change\n",
        "previous_review_feedback": "Use the saved reviewer note.",
    }
    ticket = Ticket.from_dict(payload)

    prompt = build_file_rewrite_prompt(ticket, "calc.py")

    assert "Fix the edge case." in prompt
    assert "Use the saved reviewer note." in prompt
    assert "Your previous diff was REJECTED" in prompt
    assert "+bad change" in prompt
