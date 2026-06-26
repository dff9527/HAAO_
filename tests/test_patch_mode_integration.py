from __future__ import annotations

import copy
import sys
from pathlib import Path

from clients.lmstudio import ChatMessage
from orchestrator.db.sqlite import TicketRepository, connect
from orchestrator.execution_loop import ExecutionLoop


def _patch_block(search: str, replacement: str) -> str:
    return (
        "<<<<<<< SEARCH\n"
        f"{search}\n"
        "=======\n"
        f"{replacement}\n"
        ">>>>>>> REPLACE"
    )
from orchestrator.models.ticket import Ticket
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


class QueuedLocalModel:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        content = messages[0].content if isinstance(messages[0], ChatMessage) else messages[0]["content"]
        self.prompts.append(content)
        if not self.outputs:
            raise AssertionError("No queued model outputs remain")
        return self.outputs.pop(0)


def _large_file_content() -> tuple[str, str]:
    marker = "# UNTOUCHED_REGION_MARKER\n"
    padding = marker + ("# filler line\n" * 1200)
    broken = f"{padding}\ndef compute():\n    return 0\n"
    fixed = f"{padding}\ndef compute():\n    return 1\n"
    return broken, fixed


def _large_file_ticket_payload(fresh_ticket_dict: dict, *, broken: str, retry_budget: int) -> dict:
    payload = copy.deepcopy(fresh_ticket_dict)
    payload.pop("result", None)
    payload.update(
        {
            "id": "T-950",
            "status": "ready",
            "task": {
                "description": "Fix compute() in a synthetic large file via patch blocks.",
                "target_files": ["large_module.py"],
                "constraints": ["Only edit large_module.py"],
            },
            "context": {"files": [{"path": "large_module.py", "content": broken}]},
            "definition_of_done": {
                "tests": [
                    {
                        "command": (
                            f"{sys.executable} -c \"import pathlib; "
                            "p=pathlib.Path('large_module.py'); "
                            "assert '# UNTOUCHED_REGION_MARKER' in p.read_text(); "
                            "import large_module; assert large_module.compute() == 1\""
                        ),
                        "expect": "pass",
                        "timeout_sec": 5,
                    }
                ],
                "acceptance_criteria": ["compute returns 1 and untouched padding remains"],
            },
            "execution": {
                "assigned_model": "qwen3-coder-next",
                "retry_budget": retry_budget,
                "attempts": 0,
                "escalate_to": "tech_lead",
            },
        }
    )
    return payload


def _valid_patch_output() -> str:
    return _patch_block(
        "def compute():\n    return 0",
        "def compute():\n    return 1",
    )


def _destructive_patch_output(broken: str) -> str:
    return _patch_block(broken, "pass\n")


def _fenced_bad_format_output() -> str:
    return (
        "```python\n"
        "# large_module.py\n"
        "<<<<<<< SEARCH\n"
        "not in file\n"
        "=======\n"
        "noop\n"
        ">>>>>>> REPLACE\n"
        "```\n"
    )


def test_large_file_patch_passes_dod_in_worktree_and_reaches_diff_pending(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=0))
    )
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        QueuedLocalModel([_valid_patch_output()]),
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken
    assert "return 1" in result.ticket.result.diff
    assert "# UNTOUCHED_REGION_MARKER" in (workspace_repo / "large_module.py").read_text(encoding="utf-8")


def test_invalid_patch_retries_with_feedback_then_passes(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    model = QueuedLocalModel(
        [
            "<<<<<<< SEARCH\nnot in file\n=======\nnoop\n>>>>>>> REPLACE",
            _valid_patch_output(),
        ]
    )
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert result.ticket.execution.attempts == 1
    assert len(model.prompts) == 2
    assert "Previous test output" not in model.prompts[0]
    assert "Previous test output" in model.prompts[1]
    assert "SEARCH block 1 was not found" in model.prompts[1]


def test_invalid_patch_exhausts_budget_and_blocks(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    bad_patch = "<<<<<<< SEARCH\nmissing\n=======\nstill missing\n>>>>>>> REPLACE"
    model = QueuedLocalModel([bad_patch, bad_patch])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is False
    assert result.escalated is True
    assert repository.get("T-950").status == "blocked"
    assert repository.get("T-950").execution.attempts == 2
    assert len(model.prompts) == 2
    assert "Previous test output" in model.prompts[1]


def test_destructive_patch_retries_with_good_block_and_passes(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    model = QueuedLocalModel([_destructive_patch_output(broken), _valid_patch_output()])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken
    assert "Edit rejected as destructive" in model.prompts[1]
    assert "return 1" in result.ticket.result.diff


def test_persistent_destructive_patch_blocks_without_corrupting_file(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    destructive = _destructive_patch_output(broken)
    model = QueuedLocalModel([destructive, destructive])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is False
    assert result.escalated is True
    assert repository.get("T-950").status == "blocked"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken
    assert "import error" not in (repository.get("T-950").result.test_output or "").lower()
    assert "Edit rejected as destructive" in (repository.get("T-950").result.test_output or "")


def test_fenced_bad_format_retries_then_passes_in_worktree(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    model = QueuedLocalModel([_fenced_bad_format_output(), _valid_patch_output()])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken


def test_destructive_patch_retries_with_good_block_and_passes(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    model = QueuedLocalModel([_destructive_patch_output(broken), _valid_patch_output()])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken
    assert "Edit rejected as destructive" in model.prompts[1]
    assert "return 1" in result.ticket.result.diff


def test_persistent_destructive_patch_blocks_without_corrupting_file(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    destructive = _destructive_patch_output(broken)
    model = QueuedLocalModel([destructive, destructive])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is False
    assert result.escalated is True
    assert repository.get("T-950").status == "blocked"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken
    assert "import error" not in (repository.get("T-950").result.test_output or "").lower()
    assert "Edit rejected as destructive" in (repository.get("T-950").result.test_output or "")


def test_fenced_bad_format_retries_then_passes_in_worktree(
    workspace_repo: Path,
    fresh_ticket_dict: dict,
) -> None:
    broken, _fixed = _large_file_content()
    (workspace_repo / "large_module.py").write_text(broken, encoding="utf-8")
    init_git_repo(workspace_repo)

    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(
        Ticket.from_dict(_large_file_ticket_payload(fresh_ticket_dict, broken=broken, retry_budget=1))
    )
    model = QueuedLocalModel([_fenced_bad_format_output(), _valid_patch_output()])
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )

    result = loop.run_ticket("T-950")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert (workspace_repo / "large_module.py").read_text(encoding="utf-8") == broken
