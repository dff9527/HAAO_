from pathlib import Path

from orchestrator.context.repo_context import build_enriched_repo_context


def test_build_enriched_repo_context_includes_summary_tree_and_scope(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add_one(v):\n    return v\n", encoding="utf-8")
    (repo / "README.md").write_text("# HAAO\n", encoding="utf-8")

    context = build_enriched_repo_context(repo, ["src/calc.py"])

    assert "Repository summary:" in context
    assert "File tree (relative paths):" in context
    assert "Recent files (by mtime):" in context
    assert "Scoped file snapshots:" in context
    assert "src/calc.py" in context
    assert "def add_one" in context


def test_build_enriched_repo_context_without_scope_still_has_overview(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")

    context = build_enriched_repo_context(repo, [])

    assert "Repository summary:" in context
    assert "app.py" in context
    assert "Scoped file snapshots:" not in context
