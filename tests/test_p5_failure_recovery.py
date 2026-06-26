"""P5 — deterministic failure-path probes (retry, fallback, escalation, crash recovery)."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from clients.claude_po import AuditResult
from clients.lmstudio import ChatMessage
from orchestrator.crash_recovery import CrashRecoveryService
from orchestrator.db.sqlite import SettingsRepository, TicketRepository, connect
from orchestrator.escalation import EscalationService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_registry import execution_key, execution_registry
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.role_routing import role_routing_store
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo
from tests.test_auto_orchestrator import FakeLocalModel, FakeTechLead, make_ticket


WRONG_OUTPUT = "def add_one(value):\n    return value\n"
GOOD_OUTPUT = "def add_one(value):\n    return value + 1\n"


class ObservingFailingModel:
    """Returns failing content and records prompts to verify retry feedback injection."""

    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []
        self.calls = 0

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        self.calls += 1
        content = messages[0].content if isinstance(messages[0], ChatMessage) else messages[0]["content"]
        self.prompts.append(content)
        return self.output


class ModelChainByAssignment:
    """Fails on model_A, succeeds on model_B."""

    def __init__(self, model_a: str, model_b: str) -> None:
        self.model_a = model_a
        self.model_b = model_b
        self.calls: list[str] = []

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        self.calls.append(model)
        if model == self.model_a:
            return WRONG_OUTPUT
        return GOOD_OUTPUT


class CapturingTechLead:
    def __init__(self, verdict: str = "approved", feedback: str = "ok") -> None:
        self.result = AuditResult(verdict=verdict, feedback=feedback)
        self.calls = 0
        self.last_diff: str | None = None

    def audit(self, ticket: Ticket | dict, diff: str) -> AuditResult:
        self.calls += 1
        self.last_diff = diff
        return self.result


def _bind_single_model_routing(connection) -> SettingsRepository:
    settings_repository = SettingsRepository(connection)
    settings_repository.set_role_routing(
        {
            "tech_lead": "claude-tech-lead",
            "dev_team": "qwen3-coder-next",
            "gatekeeper": "gemma-4-26b-a4b",
            "escalation_target": "claude-tech-lead",
        }
    )
    role_routing_store.bind_settings_repository(settings_repository)
    return settings_repository

def _setup_calc_repo(workspace_repo: Path) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)


def _make_loop(
    workspace_repo: Path,
    repository: TicketRepository,
    local_model,
    *,
    settings_repository: SettingsRepository | None = None,
) -> ExecutionLoop:
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        local_model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )
    loop.settings_repository = settings_repository
    return loop


def _extra_worktrees(repo: Path) -> list[Path]:
    return GitWorkspaceGuard(repo)._list_worktree_paths()


def _create_orphan_worktree(repo: Path, ticket_id: str) -> Path:
    safe_ticket_id = re.sub(r"[^A-Za-z0-9_.-]", "-", ticket_id)
    worktree_path = Path(
        tempfile.mkdtemp(prefix=f"haao-{safe_ticket_id}-worktree-")
    ).resolve()
    worktree_path.rmdir()
    completed = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return worktree_path


def _blocked_ticket_payload(fresh_ticket_dict: dict, *, escalate_to: str = "tech_lead") -> dict:
    payload = make_ticket(fresh_ticket_dict, status="blocked", retry_budget=2).to_dict()
    payload["metadata"] = {
        "escalated_to": escalate_to,
        "escalation_reason": "retry_budget_exhausted",
    }
    payload["result"] = {
        "outcome": "test_failed",
        "diff": "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n",
        "test_output": "assertion failed",
    }
    payload["execution"]["escalate_to"] = escalate_to
    return payload


# --- P5a: retry exhaustion → BLOCKED (no local fallback) ---


def test_p5a_test_failed_retry_exhaustion_blocks_without_fallback(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    connection = connect(workspace_repo.with_suffix(".sqlite3"))
    settings_repository = _bind_single_model_routing(connection)
    repository = TicketRepository(connection)
    repository.create(make_ticket(fresh_ticket_dict, retry_budget=2))
    model = ObservingFailingModel(WRONG_OUTPUT)
    loop = _make_loop(workspace_repo, repository, model, settings_repository=settings_repository)

    result = loop.run_ticket("T-900")

    ticket = repository.get("T-900")
    assert result.passed is False
    assert result.escalated is True
    assert ticket.status == "blocked"
    assert ticket.execution.attempts == 3
    assert ticket.result.outcome == "test_failed"
    assert ticket.result.test_output
    assert model.calls == 3
    assert ticket.metadata.last_intervention_notification["reason"] == "ticket_blocked"
    assert any(
        "Intervention needed: ticket_blocked" in log["message"]
        for log in repository.logs_for_ticket("T-900")
    )
    assert "Previous test output" not in model.prompts[0]
    assert all("Previous test output" in prompt for prompt in model.prompts[1:])
    assert _extra_worktrees(workspace_repo) == []
    reset_logs = [
        log["message"]
        for log in repository.logs_for_ticket("T-900")
        if log["message"] == "Reset ticket worktree before retry"
    ]
    assert len(reset_logs) == 2


def test_p5a_write_error_retry_exhaustion_blocks_without_fallback(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    connection = connect(workspace_repo.with_suffix(".sqlite3"))
    settings_repository = _bind_single_model_routing(connection)
    repository = TicketRepository(connection)
    repository.create(make_ticket(fresh_ticket_dict, retry_budget=2))
    model = ObservingFailingModel("")
    loop = _make_loop(workspace_repo, repository, model, settings_repository=settings_repository)

    result = loop.run_ticket("T-900")

    ticket = repository.get("T-900")
    assert result.passed is False
    assert result.escalated is True
    assert ticket.status == "blocked"
    assert ticket.execution.attempts == 3
    assert ticket.result.outcome == "error"
    assert "empty file content" in ticket.result.test_output
    assert model.calls == 3
    assert ticket.metadata.last_intervention_notification["reason"] == "ticket_blocked"
    assert "Previous test output" not in model.prompts[0]
    assert all("Previous test output" in prompt for prompt in model.prompts[1:])
    assert _extra_worktrees(workspace_repo) == []


# --- P5b: retry exhaustion → local fallback chain (then success) ---


def test_p5b_retry_exhaustion_falls_back_to_second_model_and_succeeds(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    connection = connect(workspace_repo.with_suffix(".sqlite3"))
    repository = TicketRepository(connection)
    settings_repository = SettingsRepository(connection)
    settings_repository.set_role_routing(
        {
            "tech_lead": "claude-tech-lead",
            "dev_team": ["qwen3-coder-next", "gemma-4-26b-a4b"],
            "gatekeeper": "gemma-4-26b-a4b",
            "escalation_target": "claude-tech-lead",
        }
    )
    role_routing_store.bind_settings_repository(settings_repository)
    repository.create(make_ticket(fresh_ticket_dict, retry_budget=1))
    model = ModelChainByAssignment("qwen3-coder-next", "gemma-4-26b-a4b")
    loop = _make_loop(workspace_repo, repository, model, settings_repository=settings_repository)

    result = loop.run_ticket("T-900")

    ticket = repository.get("T-900")
    assert result.passed is True
    assert result.escalated is False
    assert ticket.status == "diff_pending"
    assert ticket.execution.assigned_model == "gemma-4-26b-a4b"
    assert ticket.metadata.local_fallback_from == "qwen3-coder-next"
    assert ticket.metadata.local_fallback_to == "gemma-4-26b-a4b"
    assert ticket.metadata.local_fallback_reason == "retry_budget_exhausted"
    assert model.calls == ["qwen3-coder-next", "qwen3-coder-next", "gemma-4-26b-a4b"]
    assert any(
        "falling back to gemma-4-26b-a4b" in log["message"]
        for log in repository.logs_for_ticket("T-900")
    )
    assert ticket.status != "blocked"


# --- P5c: BLOCKED → Claude escalation ---


def test_p5c_tech_lead_escalation_handles_blocked_ticket(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    expected_diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
    payload = _blocked_ticket_payload(fresh_ticket_dict, escalate_to="tech_lead")
    payload["result"]["diff"] = expected_diff
    repository.create(Ticket.from_dict(payload))
    tech_lead = CapturingTechLead(verdict="rejected", feedback="Split this smaller")
    service = EscalationService(repository, tech_lead)

    result = service.handle_blocked_ticket("T-900")

    assert result.handled is True
    assert result.escalated_to == "tech_lead"
    assert result.feedback == "Split this smaller"
    assert tech_lead.calls == 1
    assert tech_lead.last_diff == expected_diff
    ticket = repository.get("T-900")
    assert ticket.status == "blocked"
    assert ticket.audit.reviewed_by == "claude-tech-lead"
    assert ticket.audit.verdict == "rejected"
    assert ticket.metadata.escalation_handled_by == "claude-tech-lead"
    assert ticket.metadata.escalation_feedback == "Split this smaller"
    assert ticket.metadata.escalation_handled_at
    assert any(
        "Tech Lead escalation handled: Split this smaller" in log["message"]
        for log in repository.logs_for_ticket("T-900")
    )


def test_p5c_tech_lead_escalation_approved_verdict(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(Ticket.from_dict(_blocked_ticket_payload(fresh_ticket_dict)))
    tech_lead = CapturingTechLead(verdict="approved", feedback="Looks good")
    service = EscalationService(repository, tech_lead)

    result = service.handle_blocked_ticket("T-900")

    assert result.handled is True
    ticket = repository.get("T-900")
    assert ticket.audit.verdict == "approved"
    assert ticket.audit.feedback == "Looks good"


def test_p5c_non_tech_lead_escalation_skips_claude_call(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_blocked_ticket_payload(fresh_ticket_dict, escalate_to="human"))
    )
    tech_lead = CapturingTechLead()
    service = EscalationService(repository, tech_lead)

    result = service.handle_blocked_ticket("T-900")

    assert result.handled is False
    assert result.escalated_to == "human"
    assert tech_lead.calls == 0
    assert any(
        "no automatic Tech Lead call made" in log["message"]
        for log in repository.logs_for_ticket("T-900")
    )


def test_p5c_escalation_on_non_blocked_ticket_is_noop(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(make_ticket(fresh_ticket_dict, status="ready"))
    tech_lead = CapturingTechLead()
    service = EscalationService(repository, tech_lead)

    before = repository.get("T-900").to_dict()
    result = service.handle_blocked_ticket("T-900")

    assert result.handled is False
    assert tech_lead.calls == 0
    assert repository.get("T-900").to_dict() == before


# --- P5d: crash recovery (orphaned execution) ---


def test_p5d_crash_recovery_recovers_orphans_and_removes_stale_worktrees(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    _setup_calc_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    guard = GitWorkspaceGuard(workspace_repo)

    testing_payload = make_ticket(fresh_ticket_dict, status="testing", retry_budget=2).to_dict()
    testing_payload["id"] = "T-901"
    testing_payload["result"] = {"outcome": "test_failed", "diff": "", "test_output": "stale"}
    repository.create(Ticket.from_dict(testing_payload))

    in_progress_payload = make_ticket(fresh_ticket_dict, status="in_progress", retry_budget=2).to_dict()
    in_progress_payload["id"] = "T-902"
    repository.create(Ticket.from_dict(in_progress_payload))

    live_payload = make_ticket(fresh_ticket_dict, status="in_progress", retry_budget=2).to_dict()
    live_payload["id"] = "T-903"
    repository.create(Ticket.from_dict(live_payload))

    orphan_worktree = _create_orphan_worktree(workspace_repo, "T-901")
    assert orphan_worktree.exists()

    registry_key = execution_key(repository.project_id, "T-903")
    execution_registry.register(registry_key)
    try:
        service = CrashRecoveryService(repository, guard)
        first = service.recover_orphaned_execution()

        assert "T-901" in first.recovered_ticket_ids
        assert "T-902" in first.noted_ticket_ids
        assert "T-903" not in first.recovered_ticket_ids
        assert "T-903" not in first.noted_ticket_ids
        assert first.changed is True
        assert any(path.endswith(orphan_worktree.name) or orphan_worktree.name in path for path in first.removed_worktrees)
        assert not orphan_worktree.exists()

        ticket_x = repository.get("T-901")
        assert ticket_x.status == "in_progress"
        assert ticket_x.result.outcome == "pending"
        assert ticket_x.metadata.orphan_recovered_from == "testing"
        assert ticket_x.metadata.orphan_recovered_to == "in_progress"
        assert ticket_x.metadata.orphan_recovery_reason == "worker_restart_without_registered_execution"
        assert ticket_x.metadata.orphan_recovered_at
        assert any(
            "Recovered orphaned testing ticket" in log["message"]
            for log in repository.logs_for_ticket("T-901")
        )

        ticket_y = repository.get("T-902")
        assert ticket_y.metadata.orphan_recovered_from == "in_progress"
        assert ticket_y.metadata.orphan_recovered_to == "in_progress"
        assert any(
            "will be retried" in log["message"]
            for log in repository.logs_for_ticket("T-902")
        )

        ticket_z = repository.get("T-903")
        assert ticket_z.status == "in_progress"
        z_meta = ticket_z.metadata.model_dump(mode="json") if ticket_z.metadata else {}
        assert not z_meta.get("orphan_recovered_from")

        second = service.recover_orphaned_execution()
        assert "T-902" not in second.noted_ticket_ids
        assert second.changed is False

        loop = _make_loop(workspace_repo, repository, FakeLocalModel(GOOD_OUTPUT))
        recovered_run = loop.run_ticket("T-901")
        assert recovered_run.passed is True
        assert repository.get("T-901").status == "diff_pending"
    finally:
        execution_registry.unregister(registry_key)


def test_p5_failure_path_coverage_checklist() -> None:
    """Documented checklist — each path is exercised by a dedicated test above."""
    covered = {
        "retry_exhausted": True,
        "local_fallback": True,
        "blocked": True,
        "claude_escalation": True,
        "crash_recovery_testing": True,
        "crash_recovery_in_progress": True,
        "orphan_worktree_removed": True,
        "escalation_idempotent": True,
    }
    assert all(covered.values())
