from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RelatedContext:
    path: str
    content: str
    symbols: list[str]
    reason: str


def retrieve_related_context(
    repo_root: str | Path,
    target_files: list[str],
    *,
    max_files: int = 3,
) -> list[RelatedContext]:
    root = Path(repo_root).resolve()
    related: list[RelatedContext] = []
    seen = {Path(path).as_posix() for path in target_files}

    for target in target_files:
        if len(related) >= max_files:
            break
        target_path = (root / target).resolve()
        if not target_path.is_relative_to(root) or target_path.suffix != ".py" or not target_path.is_file():
            continue

        for module_name in _imported_modules(target_path):
            candidate = _resolve_python_module(root, target_path.parent, module_name)
            if candidate is None:
                continue
            rel_path = candidate.relative_to(root).as_posix()
            if rel_path in seen:
                continue
            seen.add(rel_path)
            content = candidate.read_text(encoding="utf-8")
            related.append(
                RelatedContext(
                    path=rel_path,
                    content=content,
                    symbols=_defined_symbols(content),
                    reason=f"Related import from {target}",
                )
            )
            if len(related) >= max_files:
                break

    return related


def _imported_modules(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(("." * node.level) + node.module)
    return modules


def _resolve_python_module(root: Path, target_dir: Path, module_name: str) -> Path | None:
    search_roots = [root]
    if module_name.startswith("."):
        module_name = module_name.lstrip(".")
        search_roots.insert(0, target_dir)
    parts = module_name.split(".")

    for search_root in search_roots:
        candidate = (search_root / Path(*parts)).with_suffix(".py").resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        package_init = (search_root / Path(*parts) / "__init__.py").resolve()
        if package_init.is_relative_to(root) and package_init.is_file():
            return package_init
    return None


def _defined_symbols(content: str) -> list[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
    return symbols
