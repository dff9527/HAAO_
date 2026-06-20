from __future__ import annotations

import re
import subprocess
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
PLUS_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


class DiffScopeError(ValueError):
    """Raised when a unified diff touches files outside the allowed target set."""


def normalize_repo_path(path: str) -> str:
    normalized = Path(path.replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def extract_diff_paths(diff: str) -> set[str]:
    paths: set[str] = set()
    for line in diff.splitlines():
        match = DIFF_GIT_RE.match(line)
        if match:
            paths.add(normalize_repo_path(match.group(2)))
            continue
        match = PLUS_FILE_RE.match(line)
        if match:
            candidate = match.group(1)
            if candidate != "dev/null":
                paths.add(normalize_repo_path(candidate))
    return paths


def validate_diff_target_files(diff: str, target_files: list[str]) -> None:
    allowed = {normalize_repo_path(path) for path in target_files}
    touched = extract_diff_paths(diff)
    if not touched:
        raise DiffScopeError("Diff does not declare any changed files")

    extra = touched - allowed
    if extra:
        raise DiffScopeError(
            "Diff touches files outside ticket.task.target_files: "
            + ", ".join(sorted(extra))
        )


class GitWorkspaceGuard:
    """Protect the main repository while ticket execution happens in worktrees."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def is_dirty(self) -> bool:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        if completed.returncode != 0:
            return False
        return bool(completed.stdout.strip())

    def rollback(self) -> None:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )

    def ensure_clean_for_retry(self) -> None:
        if not self.is_dirty():
            return
        self.rollback()
        if self.is_dirty():
            raise RuntimeError("Workspace is dirty and could not be rolled back for retry")

    @contextmanager
    def worktree_for_ticket(self, ticket_id: str) -> Iterator[Path]:
        safe_ticket_id = re.sub(r"[^A-Za-z0-9_.-]", "-", ticket_id)
        worktree_path = Path(
            tempfile.mkdtemp(prefix=f"haao-{safe_ticket_id}-worktree-")
        ).resolve()
        # git worktree add requires the target path not to exist.
        worktree_path.rmdir()
        completed = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Could not create git worktree for ticket execution: "
                + completed.stderr.strip()
            )
        try:
            yield worktree_path
        finally:
            self.remove_worktree(worktree_path)

    def reset_worktree(self, worktree_path: str | Path) -> None:
        path = Path(worktree_path).resolve()
        if not path.exists():
            return
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            shell=False,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=path,
            capture_output=True,
            text=True,
            shell=False,
        )

    def apply_unified_diff(self, diff: str, target_files: list[str]) -> None:
        if not diff.strip():
            raise DiffScopeError("No diff to apply")
        validate_diff_target_files(diff, target_files)
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            cwd=self.repo_root,
            input=diff,
            capture_output=True,
            text=True,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "git apply failed: " + completed.stderr.strip()
            )

    def remove_worktree(self, worktree_path: str | Path) -> None:
        path = Path(worktree_path).resolve()
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        if path.exists():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()

    def remove_orphaned_ticket_worktrees(self, ticket_ids: list[str]) -> list[Path]:
        ticket_slugs = {
            re.sub(r"[^A-Za-z0-9_.-]", "-", ticket_id) for ticket_id in ticket_ids
        }
        if not ticket_slugs:
            return []

        removed: list[Path] = []
        for worktree_path in self._list_worktree_paths():
            name = worktree_path.name
            if not any(
                name.startswith(f"haao-{ticket_slug}-worktree-")
                for ticket_slug in ticket_slugs
            ):
                continue
            self.remove_worktree(worktree_path)
            removed.append(worktree_path)
        return removed

    def _list_worktree_paths(self) -> list[Path]:
        completed = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        if completed.returncode != 0:
            return []

        paths: list[Path] = []
        for line in completed.stdout.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ")).resolve()
                if path != self.repo_root:
                    paths.append(path)
        return paths
