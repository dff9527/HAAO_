from pathlib import Path

import pytest

from orchestrator.context.injector import (
    ContextInjector,
    estimate_tokens,
    truncate_to_token_budget,
)
from orchestrator.models.ticket import Context, ContextFile, Task, Ticket, TicketStatus


def _ticket_with_targets(target_files: list[str]) -> Ticket:
    return Ticket(
        id="T-100",
        title="Inject context",
        type="chore",
        status=TicketStatus.BACKLOG,
        task=Task(description="Read files", target_files=target_files),
        context=Context(files=[]),
        definition_of_done={"tests": [{"command": "pytest -q", "expect": "pass"}]},
        execution={"assigned_model": "qwen3-coder-next", "retry_budget": 1},
        audit={"verdict": "pending"},
    )


def test_inject_populates_file_contents(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("def main():\n    return 42\n", encoding="utf-8")

    ticket = _ticket_with_targets(["src/app.py"])
    injector = ContextInjector(repo)
    updated = injector.inject(ticket)

    assert len(updated.context.files) == 1
    assert updated.context.files[0].path == "src/app.py"
    assert "return 42" in updated.context.files[0].content
    assert updated.context.files[0].truncated is False
    assert updated.context.token_estimate == estimate_tokens(updated.context.files[0].content)


def test_b039_inject_adds_related_import_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    package = repo / "src"
    package.mkdir()
    (package / "app.py").write_text(
        "from src.helpers import normalize\n\n"
        "def main(value):\n"
        "    return normalize(value)\n",
        encoding="utf-8",
    )
    (package / "helpers.py").write_text(
        "def normalize(value):\n"
        "    return value.strip().lower()\n",
        encoding="utf-8",
    )

    ticket = _ticket_with_targets(["src/app.py"])
    updated = ContextInjector(repo).inject(ticket)

    assert [file.path for file in updated.context.files] == ["src/app.py", "src/helpers.py"]
    assert "return value.strip().lower()" in updated.context.files[1].content
    assert "src/helpers.py:normalize" in updated.context.related_symbols


def test_inject_truncates_when_token_budget_exceeded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "big.py"
    target.write_text("x" * 400, encoding="utf-8")

    ticket = _ticket_with_targets(["big.py"])
    injector = ContextInjector(repo, max_tokens=10)
    updated = injector.inject(ticket)

    assert updated.context.files[0].truncated is True
    assert len(updated.context.files[0].content) < 400
    assert updated.context.token_estimate <= 10


def test_inject_rejects_paths_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    ticket = _ticket_with_targets(["../outside.txt"])
    injector = ContextInjector(repo)

    with pytest.raises(ValueError, match="outside repo root"):
        injector.inject(ticket)


def test_truncate_to_token_budget() -> None:
    text = "a" * 100
    truncated = truncate_to_token_budget(text, token_budget=5)
    assert len(truncated) == 20
