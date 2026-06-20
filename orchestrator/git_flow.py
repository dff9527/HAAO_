from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.execution_safety import DiffScopeError, GitWorkspaceGuard
from orchestrator.models.ticket import Ticket


@dataclass(frozen=True)
class TicketCommitResult:
    branch: str
    commit: str
    base_branch: str


@dataclass(frozen=True)
class TicketMergeResult:
    branch: str
    base_branch: str
    merge_commit: str


@dataclass(frozen=True)
class TicketRevertResult:
    base_branch: str
    reverted_commit: str
    revert_commit: str


class GitTicketFlow:
    def __init__(
        self,
        repo_root: str | Path,
        *,
        workspace_guard: GitWorkspaceGuard | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.workspace_guard = workspace_guard or GitWorkspaceGuard(self.repo_root)

    def approve_diff_to_branch(self, ticket: Ticket, diff: str) -> TicketCommitResult:
        if not diff.strip():
            raise DiffScopeError("Ticket has no pending diff to approve")
        self._ensure_clean("approve ticket diff")

        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        stored_base = metadata.get("git_base_branch")
        base_branch = (
            stored_base
            if isinstance(stored_base, str) and stored_base.strip()
            else self.current_branch() or "HEAD"
        )
        branch = _ticket_branch(ticket.id)
        if base_branch and base_branch != "HEAD":
            self.checkout(base_branch)
        if self.branch_exists(branch):
            self.run(["git", "checkout", "-B", branch, base_branch])
        else:
            self.run(["git", "checkout", "-b", branch, base_branch])

        try:
            self.workspace_guard.apply_unified_diff(diff, ticket.task.target_files)
            self.run(["git", "add", "--", *ticket.task.target_files])
            if self._is_index_clean():
                raise RuntimeError("Approved diff produced no staged changes")

            self.run(["git", "commit", "-m", self._commit_message(ticket)])
            commit = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        finally:
            # Return the main repo to its base branch (A2). The ticket branch keeps
            # the commit for the later merge step; leaving the repo checked out on the
            # ticket branch corrupts the base that subsequent tickets branch from.
            if base_branch and base_branch != "HEAD":
                self.checkout(base_branch)
        return TicketCommitResult(branch=branch, commit=commit, base_branch=base_branch)

    def merge_ticket_branch(self, ticket: Ticket) -> TicketMergeResult:
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        branch = metadata.get("git_branch")
        base_branch = metadata.get("git_base_branch")
        if not isinstance(branch, str) or not branch:
            raise ValueError("Ticket has no git_branch metadata to merge")
        if not isinstance(base_branch, str) or not base_branch:
            raise ValueError("Ticket has no git_base_branch metadata to merge")

        self._ensure_clean("merge ticket branch")
        self.checkout(base_branch)
        self.run(["git", "merge", "--no-ff", branch, "-m", f"Merge {branch}"])
        merge_commit = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        return TicketMergeResult(
            branch=branch,
            base_branch=base_branch,
            merge_commit=merge_commit,
        )

    def revert_ticket_merge(self, ticket: Ticket) -> TicketRevertResult:
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        base_branch = metadata.get("git_merged_to") or metadata.get("git_base_branch")
        merge_commit = metadata.get("git_merge_commit")
        if not isinstance(base_branch, str) or not base_branch:
            raise ValueError("Ticket has no git_merged_to metadata to revert")
        if not isinstance(merge_commit, str) or not merge_commit:
            raise ValueError("Ticket has no git_merge_commit metadata to revert")

        self._ensure_clean("revert ticket merge")
        self.checkout(base_branch)
        self.run(["git", "revert", "-m", "1", "--no-edit", merge_commit])
        revert_commit = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        return TicketRevertResult(
            base_branch=base_branch,
            reverted_commit=merge_commit,
            revert_commit=revert_commit,
        )

    def current_branch(self) -> str:
        completed = self.run(["git", "branch", "--show-current"])
        return completed.stdout.strip()

    def branch_exists(self, branch: str) -> bool:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        return completed.returncode == 0

    def checkout(self, branch: str) -> None:
        self.run(["git", "checkout", branch])

    def run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            args,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
            env=_git_env(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Git command failed: "
                + " ".join(args)
                + "\n"
                + completed.stderr.strip()
            )
        return completed

    def _ensure_clean(self, action: str) -> None:
        if self.workspace_guard.is_dirty():
            raise RuntimeError(f"Cannot {action}: repository has uncommitted changes")

    def _is_index_clean(self) -> bool:
        completed = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
        return completed.returncode == 0

    def _commit_message(self, ticket: Ticket) -> str:
        title = ticket.title.strip() or ticket.id
        return f"{ticket.id}: {title}"


def _ticket_branch(ticket_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", ticket_id)
    return f"haao/{safe}"


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "HAAO"),
        "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "haao@example.local"),
        "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "HAAO"),
        "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "haao@example.local"),
    }


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
