import pytest

from orchestrator.execution_safety import (
    DiffScopeError,
    GitWorkspaceGuard,
    extract_diff_paths,
    normalize_repo_path,
    validate_diff_target_files,
)
from tests.conftest import init_git_repo, workspace_repo


CALC_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add_one(value):
-    return value
+    return value + 1
"""

BAD_DIFF = """diff --git a/other.py b/other.py
--- a/other.py
+++ b/other.py
@@ -1 +1 @@
-old
+new
"""


def test_extract_diff_paths() -> None:
    assert extract_diff_paths(CALC_DIFF) == {"calc.py"}


def test_normalize_repo_path_preserves_parent_traversal() -> None:
    assert normalize_repo_path("./calc.py") == "calc.py"
    assert normalize_repo_path("../other.py") == "../other.py"


def test_validate_diff_target_files_accepts_allowed_paths() -> None:
    validate_diff_target_files(CALC_DIFF, ["calc.py"])


def test_validate_diff_target_files_rejects_out_of_scope_paths() -> None:
    with pytest.raises(DiffScopeError, match="outside ticket.task.target_files"):
        validate_diff_target_files(BAD_DIFF, ["calc.py"])


def test_git_workspace_guard_rolls_back_dirty_changes(workspace_repo) -> None:
    target = workspace_repo / "calc.py"
    target.write_text("original\n", encoding="utf-8")
    init_git_repo(workspace_repo)
    target.write_text("dirty\n", encoding="utf-8")

    guard = GitWorkspaceGuard(workspace_repo)
    assert guard.is_dirty() is True
    guard.ensure_clean_for_retry()

    assert guard.is_dirty() is False
    assert target.read_text(encoding="utf-8") == "original\n"
