from __future__ import annotations

from pathlib import Path


def validate_scope_paths(scope_paths: list[str]) -> None:
    for scope in scope_paths:
        path = Path(scope)
        if path.is_absolute():
            raise ValueError(f"Scope path must be relative to repo root: {scope}")
        if ".." in path.parts:
            raise ValueError(f"Scope path cannot contain '..': {scope}")


def expand_scope(repo_root: Path, scope: str) -> list[Path]:
    if any(char in scope for char in "*?[]"):
        candidates = repo_root.glob(scope)
    else:
        candidate = (repo_root / scope).resolve()
        if candidate.is_dir():
            candidates = candidate.rglob("*")
        else:
            candidates = [candidate]

    paths: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(repo_root):
            raise ValueError(f"Scope path escapes repo root: {scope}")
        if resolved.is_file():
            paths.append(resolved)
    return sorted(paths)
