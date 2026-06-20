from __future__ import annotations

import copy
import sys
from pathlib import Path

from clients.claude_po import AuditResult
from clients.lmstudio import ChatMessage
from orchestrator.auto_orchestrator import AutoOrchestrator
from orchestrator.db.sqlite import SettingsRepository, TicketRepository, connect
from orchestrator.escalation import EscalationService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.models.ticket import Ticket
from orchestrator.review_flow import ReviewService
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.role_routing import role_routing_store
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


class FakeLocalModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        self.calls += 1
        return self.output


class FakeTechLead:
    def __init__(self, verdict: str = "approved", feedback: str = "ok") -> None:
        self.result = AuditResult(verdict=verdict, feedback=feedback)
        self.calls = 0

    def audit(self, ticket: Ticket | dict, diff: str) -> AuditResult:
        self.calls += 1
        return self.result


def make_ticket(ticket_dict: dict, *, status: str = "ready", retry_budget: int = 1) -> Ticket:
    payload = copy.deepcopy(ticket_dict)
    payload["id"] = "T-900"
    payload["title"] = "Fix add_one"
    payload["type"] = "bugfix"
    payload["status"] = status
    payload["dependencies"] = []
    payload["task"] = {
        "description": "Change add_one so it returns value + 1.",
        "target_files": ["calc.py"],
        "constraints": [],
    }
    payload["context"] = {
        "files": [
            {
                "path": "calc.py",
                "content": "def add_one(value):\n    return value\n",
            }
        ]
    }
    payload["definition_of_done"] = {
        "tests": [
            {
                "command": f"{sys.executable} -c \"import calc; assert calc.add_one(1) == 2\"",
                "expect": "pass",
                "timeout_sec": 5,
            }
        ],
        "acceptance_criteria": ["increments input"],
    }
    payload["execution"] = {
        "assigned_model": "qwen3-coder-next",
        "retry_budget": retry_budget,
        "attempts": 0,
        "escalate_to": "tech_lead",
    }
    payload["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
    payload.pop("result", None)
    return Ticket.from_dict(payload)


def make_orchestrator(
    repo: Path,
    repository: TicketRepository,
    local_model: FakeLocalModel,
    tech_lead: FakeTechLead,
) -> AutoOrchestrator:
    state_service = TicketStateService(repository)
    return AutoOrchestrator(
        repository,
        ExecutionLoop(
            repository,
            state_service,
            local_model,
            repo_root=repo,
            test_runner=TestRunner(cwd=repo),
        ),
        ReviewService(repository, state_service, tech_lead),
        EscalationService(repository, tech_lead),
        repo_root=repo,
    )


def test_auto_orchestrator_executes_ready_ticket_and_runs_technical_review(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(make_ticket(fresh_ticket_dict))
    local_model = FakeLocalModel("def add_one(value):\n    return value + 1\n")
    tech_lead = FakeTechLead(verdict="approved", feedback="technically sound")
    orchestrator = make_orchestrator(workspace_repo, repository, local_model, tech_lead)

    result = orchestrator.run_once()

    assert result.executed_ticket_ids == ["T-900"]
    assert result.reviewed_ticket_ids == []
    assert local_model.calls == 1
    assert tech_lead.calls == 0
    ticket = repository.get("T-900")
    assert ticket.status == "diff_pending"

    from orchestrator.diff_review import DiffReviewService

    DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    ).approve_diff("T-900")

    review_result = orchestrator.run_once()
    assert review_result.reviewed_ticket_ids == ["T-900"]
    assert tech_lead.calls == 1
    ticket = repository.get("T-900")
    assert ticket.status == "awaiting_acceptance"
    assert ticket.audit.feedback == "technically sound"


def test_b020_auto_orchestrator_executes_in_progress_ticket(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(make_ticket(fresh_ticket_dict, status="in_progress"))
    local_model = FakeLocalModel("def add_one(value):\n    return value + 1\n")
    tech_lead = FakeTechLead(verdict="approved", feedback="technically sound")
    orchestrator = make_orchestrator(workspace_repo, repository, local_model, tech_lead)

    result = orchestrator.run_once()

    assert result.executed_ticket_ids == ["T-900"]
    assert repository.get("T-900").status == "diff_pending"

    from orchestrator.diff_review import DiffReviewService

    DiffReviewService(
        repository,
        TicketStateService(repository),
        repo_root=workspace_repo,
    ).approve_diff("T-900")
    orchestrator.run_once()
    assert repository.get("T-900").status == "awaiting_acceptance"


def test_b045_auto_orchestrator_recovers_testing_orphan_before_execution(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(make_ticket(fresh_ticket_dict, status="testing"))
    local_model = FakeLocalModel("def add_one(value):\n    return value + 1\n")
    orchestrator = make_orchestrator(
        workspace_repo,
        repository,
        local_model,
        FakeTechLead(),
    )

    result = orchestrator.run_once()

    assert result.recovered_ticket_ids == ["T-900"]
    assert result.executed_ticket_ids == ["T-900"]
    ticket = repository.get("T-900")
    assert ticket.status == "diff_pending"
    assert ticket.metadata.orphan_recovered_from == "testing"
    assert ticket.metadata.orphan_recovered_to == "in_progress"
    assert "worker_restart_without_registered_execution" in ticket.metadata.orphan_recovery_reason
    logs = repository.logs_for_ticket("T-900")
    assert any("Recovered orphaned testing ticket" in log["message"] for log in logs)


def test_b043_diff_pending_records_intervention_notification(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(make_ticket(fresh_ticket_dict))
    orchestrator = make_orchestrator(
        workspace_repo,
        repository,
        FakeLocalModel("def add_one(value):\n    return value + 1\n"),
        FakeTechLead(),
    )

    orchestrator.run_once()

    ticket = repository.get("T-900")
    notification = ticket.metadata.last_intervention_notification
    assert notification["reason"] == "diff_review_required"
    assert notification["ticket_url"] == "/tickets/T-900"
    assert any(
        "Intervention needed: diff_review_required" in log["message"]
        for log in repository.logs_for_ticket("T-900")
    )


def test_b041_execution_falls_back_to_next_local_model_after_retry_budget(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)
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
    repository.create(make_ticket(fresh_ticket_dict, retry_budget=0))

    class ModelChain:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def chat_completion(self, *, model: str, messages, temperature: float = 0.2) -> str:
            self.calls.append(model)
            if model == "qwen3-coder-next":
                return "def add_one(value):\n    return value\n"
            return "def add_one(value):\n    return value + 1\n"

    local_model = ModelChain()
    orchestrator = make_orchestrator(
        workspace_repo,
        repository,
        local_model,
        FakeTechLead(),
    )
    orchestrator.execution_loop.settings_repository = settings_repository

    result = orchestrator.run_once()

    assert result.executed_ticket_ids == ["T-900"]
    assert local_model.calls == ["qwen3-coder-next", "gemma-4-26b-a4b"]
    ticket = repository.get("T-900")
    assert ticket.status == "diff_pending"
    assert ticket.execution.assigned_model == "gemma-4-26b-a4b"
    assert ticket.metadata.local_fallback_from == "qwen3-coder-next"
    assert ticket.metadata.local_fallback_to == "gemma-4-26b-a4b"
    assert any(
        "falling back to gemma-4-26b-a4b" in log["message"]
        for log in repository.logs_for_ticket("T-900")
    )


def test_auto_orchestrator_handles_blocked_tech_lead_escalation(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text(
        "def add_one(value):\n    return value\n",
        encoding="utf-8",
    )
    init_git_repo(workspace_repo)
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    ticket = make_ticket(fresh_ticket_dict, status="blocked")
    ticket_json = ticket.to_dict()
    ticket_json["metadata"] = {
        "escalated_to": "tech_lead",
        "escalation_reason": "retry_budget_exhausted",
    }
    ticket_json["result"] = {
        "outcome": "test_failed",
        "diff": "diff --git a/calc.py b/calc.py\n",
        "test_output": "still failing",
    }
    repository.create(Ticket.from_dict(ticket_json))
    tech_lead = FakeTechLead(verdict="rejected", feedback="Split this smaller")
    orchestrator = make_orchestrator(
        workspace_repo,
        repository,
        FakeLocalModel(""),
        tech_lead,
    )

    result = orchestrator.run_once()

    assert result.escalated_ticket_ids == ["T-900"]
    assert tech_lead.calls == 1
    ticket = repository.get("T-900")
    assert ticket.status == "blocked"
    assert ticket.audit.feedback == "Split this smaller"
    assert ticket.metadata.escalation_feedback == "Split this smaller"


def test_auto_orchestrator_skips_execution_when_workspace_is_dirty(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    (workspace_repo / "calc.py").write_text("dirty\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    (workspace_repo / "calc.py").write_text("uncommitted\n", encoding="utf-8")
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(make_ticket(fresh_ticket_dict))
    local_model = FakeLocalModel("")
    orchestrator = make_orchestrator(
        workspace_repo,
        repository,
        local_model,
        FakeTechLead(),
    )

    result = orchestrator.run_once()

    assert result.skipped_reason == "workspace_dirty"
    assert local_model.calls == 0
    assert repository.get("T-900").status == "ready"
