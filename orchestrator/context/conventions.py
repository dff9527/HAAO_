from __future__ import annotations

import json
from pathlib import Path


def detect_conventions(repo_path: str | Path) -> str:
    root = Path(repo_path).resolve()
    facts: list[str] = []

    test_command = detect_test_command(root)
    facts.append(f"Recommended test command: {test_command or '(none detected)'}")

    frameworks = detect_frameworks(root)
    facts.append("Frameworks/test tools: " + (", ".join(frameworks) if frameworks else "(none detected)"))

    test_locations = detect_test_locations(root)
    facts.append("Existing test locations: " + (", ".join(test_locations) if test_locations else "(none detected)"))

    static_checks = detect_static_checks(root)
    facts.append("Static checks: " + (", ".join(static_checks) if static_checks else "(none detected)"))

    source_hints = detect_source_hints(root)
    facts.append("Source layout hints: " + (", ".join(source_hints) if source_hints else "(none detected)"))

    return "\n".join(f"- {fact}" for fact in facts)


def detect_test_command(repo_path: str | Path) -> str:
    root = Path(repo_path).resolve()
    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
            return "npm test"

    if (root / "go.mod").exists():
        return "go test ./..."

    if _has_any(root, ["pytest.ini", "tox.ini"]) or _pyproject_mentions(root, "pytest"):
        return "pytest -q"

    if list(root.glob("test_*.py")) or list(root.glob("*_test.py")) or (root / "tests").is_dir():
        return "pytest -q"

    return ""


def detect_frameworks(repo_path: str | Path) -> list[str]:
    root = Path(repo_path).resolve()
    frameworks: list[str] = []
    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        deps: dict = {}
        if isinstance(data, dict):
            for key in ("dependencies", "devDependencies"):
                value = data.get(key, {})
                if isinstance(value, dict):
                    deps.update(value)
        for candidate in ("react", "vite", "vitest", "jest", "next"):
            if candidate in deps:
                frameworks.append(candidate)

    if (root / "go.mod").exists():
        frameworks.append("go test")
    if _has_any(root, ["pytest.ini", "tox.ini"]) or _pyproject_mentions(root, "pytest"):
        frameworks.append("pytest")
    if _pyproject_mentions(root, "fastapi"):
        frameworks.append("FastAPI")
    return sorted(set(frameworks))


def detect_test_locations(repo_path: str | Path) -> list[str]:
    root = Path(repo_path).resolve()
    locations: list[str] = []
    if (root / "tests").is_dir():
        locations.append("tests/")
    for pattern in ("test_*.py", "*_test.py", "**/*.test.ts", "**/*.test.tsx", "**/*.spec.ts", "**/*.spec.tsx"):
        for path in sorted(root.glob(pattern)):
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            if path.is_file():
                locations.append(path.relative_to(root).as_posix())
                if len(locations) >= 12:
                    return locations
    return locations


def detect_static_checks(repo_path: str | Path) -> list[str]:
    root = Path(repo_path).resolve()
    checks: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
        if "ruff" in text:
            checks.append("ruff check .")
        if "mypy" in text:
            checks.append("mypy .")
    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        if isinstance(scripts, dict):
            if "lint" in scripts:
                checks.append("npm run lint")
            if "typecheck" in scripts:
                checks.append("npm run typecheck")
    return checks


def detect_source_hints(repo_path: str | Path) -> list[str]:
    root = Path(repo_path).resolve()
    hints: list[str] = []
    for candidate in ("orchestrator", "clients", "frontend/src", "src", "app", "lib"):
        if (root / candidate).exists():
            hints.append(candidate)
    return hints


def _has_any(root: Path, names: list[str]) -> bool:
    return any((root / name).exists() for name in names)


def _pyproject_mentions(root: Path, needle: str) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    return needle.lower() in pyproject.read_text(encoding="utf-8", errors="replace").lower()
