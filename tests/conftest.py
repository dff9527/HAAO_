from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_TEST_ROOT = PROJECT_ROOT / ".pytest-workdirs"


def init_git_repo(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "--template="],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
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


@pytest.fixture
def workspace_repo() -> Iterator[Path]:
    WORKSPACE_TEST_ROOT.mkdir(exist_ok=True)
    repo = WORKSPACE_TEST_ROOT / f"repo-{uuid.uuid4().hex}"
    repo.mkdir(parents=True)
    try:
        yield repo
    finally:
        shutil.rmtree(repo, ignore_errors=True)


@pytest.fixture
def example_ticket_dict() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "atomic_ticket.example.json"
    with path.open("r", encoding="utf-8") as example_file:
        return json.load(example_file)


@pytest.fixture
def fresh_ticket_dict(example_ticket_dict: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(example_ticket_dict)
