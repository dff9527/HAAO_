from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.context.scope import expand_scope, validate_scope_paths
from orchestrator.context.untrusted import wrap_untrusted_context
from orchestrator.redaction import redact_text

DEFAULT_MAX_TREE_ENTRIES = 120
DEFAULT_MAX_RECENT_FILES = 12
DEFAULT_MAX_FILE_SNIPPET_CHARS = 4000


def build_enriched_repo_context(
    repo_root: Path,
    scope_paths: list[str],
    *,
    max_tree_entries: int = DEFAULT_MAX_TREE_ENTRIES,
    max_recent_files: int = DEFAULT_MAX_RECENT_FILES,
    max_file_snippet_chars: int = DEFAULT_MAX_FILE_SNIPPET_CHARS,
) -> str:
    """Build repo summary, file tree, recent files, and scoped snapshots for decompose."""
    repo_root = repo_root.resolve()
    sections = [
        _build_repo_summary(repo_root),
        _build_file_tree(repo_root, max_entries=max_tree_entries),
        _build_recent_files_section(repo_root, max_files=max_recent_files),
    ]

    scoped_context = _build_scoped_file_context(
        repo_root,
        scope_paths,
        max_file_snippet_chars=max_file_snippet_chars,
    )
    if scoped_context:
        sections.append(scoped_context)

    return "\n\n".join(section for section in sections if section.strip())


def _build_repo_summary(repo_root: Path) -> str:
    tracked_files = _list_repo_files(repo_root)
    top_level = sorted(
        {
            path.relative_to(repo_root).parts[0]
            for path in tracked_files
            if path.relative_to(repo_root).parts
        }
    )
    branch = _current_git_branch(repo_root)
    branch_line = f"Branch: {branch}" if branch else "Branch: (not a git repo)"
    return (
        "Repository summary:\n"
        f"- Root: {repo_root.name}\n"
        f"- {branch_line}\n"
        f"- Tracked files (approx): {len(tracked_files)}\n"
        f"- Top-level entries: {', '.join(top_level[:20]) or '(empty)'}"
    )


def _build_file_tree(repo_root: Path, *, max_entries: int) -> str:
    lines = ["File tree (relative paths):"]
    count = 0
    for path in sorted(_list_repo_files(repo_root)):
        relative = path.relative_to(repo_root).as_posix()
        depth = len(path.relative_to(repo_root).parts)
        indent = "  " * max(depth - 1, 0)
        lines.append(f"{indent}- {relative}")
        count += 1
        if count >= max_entries:
            lines.append("  ...[tree truncated]")
            break
    return "\n".join(lines)


def _build_recent_files_section(repo_root: Path, *, max_files: int) -> str:
    files = sorted(
        _list_repo_files(repo_root),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:max_files]
    if not files:
        return "Recent files:\n- (none)"

    lines = ["Recent files (by mtime):"]
    for path in files:
        relative = path.relative_to(repo_root).as_posix()
        lines.append(f"- {relative}")
    return "\n".join(lines)


def _build_scoped_file_context(
    repo_root: Path,
    scope_paths: list[str],
    *,
    max_file_snippet_chars: int,
) -> str:
    validate_scope_paths(scope_paths)
    if not scope_paths:
        return ""

    chunks = ["Scoped file snapshots:"]
    for scope in scope_paths[:20]:
        paths = expand_scope(repo_root, scope)
        if not paths:
            chunks.append(f"Scope {scope}: no matching files")
            continue
        for path in paths[:20]:
            relative = path.relative_to(repo_root)
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_file_snippet_chars:
                content = content[:max_file_snippet_chars] + "\n...[truncated]"
            chunks.append(
                f"File: {relative}\n"
                f"{wrap_untrusted_context(label=relative.as_posix(), content=redact_text(content))}"
            )
    return "\n\n".join(chunks)


def _list_repo_files(repo_root: Path) -> list[Path]:
    ignored_dirs = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
    }
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_dirs for part in path.parts):
            continue
        files.append(path)
    return files


def _current_git_branch(repo_root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None
