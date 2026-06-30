from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from orchestrator.execution_safety import extract_diff_paths, normalize_repo_path


MANIFEST_PATTERNS = (
    re.compile(r"(^|/)requirements[^/]*\.txt$"),
    re.compile(r"(^|/)package\.json$"),
    re.compile(r"(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml)$"),
    re.compile(r"(^|/)pyproject\.toml$"),
    re.compile(r"(^|/)(poetry\.lock|Pipfile|Pipfile\.lock)$"),
    re.compile(r"(^|/)(Cargo\.toml|Cargo\.lock|go\.mod|go\.sum)$"),
)
REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*([<>=!~]=?.*)?$")
JSON_DEP_RE = re.compile(r'^\s*"([^"]+)"\s*:\s*"([^"]+)"\s*,?\s*$')
TOML_DEP_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*(.+)$")
ARRAY_DEP_RE = re.compile(r'^\s*"([A-Za-z0-9_.-]+)([^"]*)"\s*,?\s*$')


@dataclass(frozen=True)
class SupplyChainFinding:
    severity: str
    source: str
    package: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "source": self.source,
            "package": self.package,
            "detail": self.detail,
        }


class SupplyChainChecker(Protocol):
    def check(self, added_deps: list[dict[str, str]], changed_manifests: list[str]) -> list[dict[str, str]]:
        ...


class NoopSupplyChainChecker:
    def check(self, added_deps: list[dict[str, str]], changed_manifests: list[str]) -> list[dict[str, str]]:
        return []


def build_supply_chain_signal(
    diff: str,
    *,
    checker: SupplyChainChecker | None = None,
) -> dict[str, list]:
    changed_manifests = sorted(path for path in extract_diff_paths(diff) if is_dependency_manifest(path))
    added_deps = _added_dependencies(diff, changed_manifests)
    findings = (checker or NoopSupplyChainChecker()).check(added_deps, changed_manifests)
    return {
        "changed_manifests": changed_manifests,
        "added_deps": added_deps,
        "findings": findings,
    }


def is_dependency_manifest(path: str) -> bool:
    normalized = normalize_repo_path(path)
    return any(pattern.search(normalized) for pattern in MANIFEST_PATTERNS)


def _added_dependencies(diff: str, manifests: list[str]) -> list[dict[str, str]]:
    manifest_set = set(manifests)
    current_path = ""
    deps: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current_path = normalize_repo_path(parts[-1].removeprefix("b/")) if parts else ""
            continue
        if current_path not in manifest_set or not line.startswith("+") or line.startswith("+++"):
            continue
        parsed = _parse_added_dependency(current_path, line[1:])
        if parsed is None:
            continue
        key = (parsed["manifest"], parsed["name"], parsed.get("version", ""))
        if key in seen:
            continue
        seen.add(key)
        deps.append(parsed)
    return deps


def _parse_added_dependency(path: str, line: str) -> dict[str, str] | None:
    basename = path.rsplit("/", 1)[-1]
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if basename.startswith("requirements") and basename.endswith(".txt"):
        match = REQ_LINE_RE.match(stripped)
        if not match:
            return None
        return {"manifest": path, "name": match.group(1), "version": (match.group(2) or "").strip()}

    if basename == "package.json":
        match = JSON_DEP_RE.match(stripped)
        if not match:
            return None
        return {"manifest": path, "name": match.group(1), "version": match.group(2)}

    if basename == "pyproject.toml":
        match = TOML_DEP_RE.match(stripped)
        if match:
            return {"manifest": path, "name": match.group(1), "version": match.group(2).strip()}
        match = ARRAY_DEP_RE.match(stripped)
        if match:
            return {"manifest": path, "name": match.group(1), "version": match.group(2).strip()}

    return None
