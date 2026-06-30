from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^P-[0-9]{3,}$|^default$")
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1)
    default_branch: str = Field(default="main", min_length=1)
    env: dict[str, str] = Field(default_factory=dict)
    env_allowlist: list[str] = Field(default_factory=lambda: ["PATH", "PYTHONPATH"])
    test_allow_network: bool = False
    sandbox_mode: Literal["auto", "docker", "unshare", "none", "strict"] = "auto"
    setup_cmd: str = ""
    cleanup_cmd: str = ""
    created_at: datetime | None = None

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key): str(env_value) for key, env_value in value.items()}

    @field_validator("env_allowlist")
    @classmethod
    def validate_env_allowlist(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            name = str(item).strip()
            if name and name not in cleaned:
                cleaned.append(name)
        return cleaned or ["PATH", "PYTHONPATH"]

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


def validate_project_path(path: str | Path) -> Path:
    repo_path = Path(path).expanduser().resolve()
    if not repo_path.exists():
        raise ValueError(f"Project path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise ValueError(f"Project path is not a directory: {repo_path}")

    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"Project path is not a git repository: {repo_path}")

    git_root = Path(completed.stdout.strip()).resolve()
    if not _same_file(git_root, repo_path):
        raise ValueError(
            f"Project path must be the git repository root: {repo_path} (root is {git_root})"
        )
    return git_root


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right
