from __future__ import annotations

import copy
import sys
from pathlib import Path

from clients.lmstudio import ChatMessage
from orchestrator.db.sqlite import TicketRepository, connect
from orchestrator.execution_loop import (
    DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
    DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
    ExecutionLoop,
    required_rewrite_output_tokens,
)
from orchestrator.models.ticket import Ticket
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService
from tests.conftest import init_git_repo


class PromptObservingModel:
    def __init__(self, output: str) -> None:
        self.output = output
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
        return self.output


def _effective_patch_mode(
    content: str,
    *,
    patch_mode_threshold_tokens: int = DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
    max_output_tokens: int = DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
) -> bool:
    required_tokens = required_rewrite_output_tokens(content)
    effective_patch_threshold = min(patch_mode_threshold_tokens, max_output_tokens)
    return required_tokens > effective_patch_threshold


def test_required_rewrite_output_tokens_scales_with_file_size() -> None:
    small = "x = 1\n"
    large = "x = 1\n" + ("# padding\n" * 2000)

    assert required_rewrite_output_tokens(small) < DEFAULT_PATCH_MODE_THRESHOLD_TOKENS
    assert _effective_patch_mode(small) is False
    assert _effective_patch_mode(large) is True


def test_small_file_uses_whole_file_prompt(workspace_repo: Path, fresh_ticket_dict: dict) -> None:
    (workspace_repo / "calc.py").write_text("def add_one(value):\n    return value\n", encoding="utf-8")
    init_git_repo(workspace_repo)

    payload = copy.deepcopy(fresh_ticket_dict)
    payload.pop("result", None)
    payload.update(
        {
            "id": "T-900",
            "status": "ready",
            "task": {
                "description": "Fix add_one",
                "target_files": ["calc.py"],
                "constraints": [],
            },
            "context": {
                "files": [
                    {
                        "path": "calc.py",
                        "content": "def add_one(value):\n    return value\n",
                    }
                ]
            },
            "definition_of_done": {
                "tests": [
                    {
                        "command": f"{sys.executable} -c \"import calc; assert calc.add_one(1) == 2\"",
                        "expect": "pass",
                        "timeout_sec": 5,
                    }
                ],
                "acceptance_criteria": [],
            },
            "execution": {
                "assigned_model": "qwen3-coder-next",
                "retry_budget": 0,
                "attempts": 0,
                "escalate_to": "tech_lead",
            },
        }
    )
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(Ticket.from_dict(payload))

    model = PromptObservingModel("def add_one(value):\n    return value + 1\n")
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )
    loop.run_ticket("T-900")

    assert model.prompts
    assert "complete updated file content" in model.prompts[0]
    assert "<<<<<<< SEARCH" not in model.prompts[0]


def test_large_file_uses_patch_prompt(workspace_repo: Path, fresh_ticket_dict: dict) -> None:
    padding = "# keep\n" * 1200
    original = f"{padding}\ndef add_one(value):\n    return value\n"
    (workspace_repo / "calc.py").write_text(original, encoding="utf-8")
    init_git_repo(workspace_repo)

    assert _effective_patch_mode(original) is True

    payload = copy.deepcopy(fresh_ticket_dict)
    payload.pop("result", None)
    payload.update(
        {
            "id": "T-901",
            "status": "ready",
            "task": {
                "description": "Fix add_one in a large file using a patch",
                "target_files": ["calc.py"],
                "constraints": [],
            },
            "context": {"files": [{"path": "calc.py", "content": original}]},
            "definition_of_done": {
                "tests": [
                    {
                        "command": f"{sys.executable} -c \"import calc; assert calc.add_one(1) == 2\"",
                        "expect": "pass",
                        "timeout_sec": 5,
                    }
                ],
                "acceptance_criteria": [],
            },
            "execution": {
                "assigned_model": "qwen3-coder-next",
                "retry_budget": 0,
                "attempts": 0,
                "escalate_to": "tech_lead",
            },
        }
    )
    repository = TicketRepository(connect(workspace_repo.with_suffix(".sqlite3")))
    repository.create(Ticket.from_dict(payload))

    patch_output = (
        "<<<<<<< SEARCH\n"
        "def add_one(value):\n"
        "    return value\n"
        "=======\n"
        "def add_one(value):\n"
        "    return value + 1\n"
        ">>>>>>> REPLACE"
    )
    model = PromptObservingModel(patch_output)
    loop = ExecutionLoop(
        repository,
        TicketStateService(repository),
        model,
        repo_root=workspace_repo,
        test_runner=TestRunner(cwd=workspace_repo),
    )
    result = loop.run_ticket("T-901")

    assert result.passed is True
    assert result.ticket.status == "diff_pending"
    assert model.prompts
    assert "<<<<<<< SEARCH" in model.prompts[0]
    assert "complete updated file content" not in model.prompts[0]
    assert (workspace_repo / "calc.py").read_text(encoding="utf-8") == original
    assert "return value + 1" in result.ticket.result.diff
