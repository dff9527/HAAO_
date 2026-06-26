from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.execution_loop import (
    DestructiveEditError,
    SearchReplaceBlockError,
    SearchReplaceParseError,
    _parse_search_replace_blocks,
    apply_search_replace_blocks,
)


def _block(search: str, replacement: str) -> str:
    return (
        "<<<<<<< SEARCH\n"
        f"{search}\n"
        "=======\n"
        f"{replacement}\n"
        ">>>>>>> REPLACE"
    )


def _large_module_body() -> str:
    return "# header\n" + ("# filler line\n" * 80) + "def compute():\n    return 0\n"


def test_apply_single_block_replaces_target_segment(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text(
        "alpha = 1\nbeta = 2\n# keep this comment\ngamma = 3\n",
        encoding="utf-8",
    )

    apply_search_replace_blocks(
        target,
        _block("beta = 2", "beta = 42"),
    )

    assert target.read_text(encoding="utf-8") == (
        "alpha = 1\nbeta = 42\n# keep this comment\ngamma = 3\n"
    )


def test_apply_multiple_blocks_in_order(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("one = 1\ntwo = 2\nthree = 3\n", encoding="utf-8")

    model_output = "\n\n".join(
        [
            _block("one = 1", "one = 10"),
            _block("three = 3", "three = 30"),
        ]
    )
    apply_search_replace_blocks(target, model_output)

    assert target.read_text(encoding="utf-8") == "one = 10\ntwo = 2\nthree = 30\n"


def test_search_not_found_raises_typed_error(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("alpha = 1\n", encoding="utf-8")

    with pytest.raises(SearchReplaceBlockError) as exc:
        apply_search_replace_blocks(target, _block("missing = 9", "missing = 0"))

    error = exc.value
    assert error.block_index == 1
    assert error.reason == "not_found"
    assert error.match_count == 0
    assert target.read_text(encoding="utf-8") == "alpha = 1\n"


def test_ambiguous_search_raises_typed_error(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("dup = 1\nmiddle\ndup = 1\n", encoding="utf-8")

    with pytest.raises(SearchReplaceBlockError) as exc:
        apply_search_replace_blocks(target, _block("dup = 1", "dup = 9"))

    error = exc.value
    assert error.block_index == 1
    assert error.reason == "ambiguous"
    assert error.match_count == 2
    assert target.read_text(encoding="utf-8") == "dup = 1\nmiddle\ndup = 1\n"


def test_no_op_replacement_is_safe(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    original = "stable = True\n"
    target.write_text(original, encoding="utf-8")

    apply_search_replace_blocks(target, _block("stable = True", "stable = True"))

    assert target.read_text(encoding="utf-8") == original


def test_parse_rejects_missing_separator() -> None:
    with pytest.raises(SearchReplaceParseError, match="missing the ======= separator"):
        _parse_search_replace_blocks("<<<<<<< SEARCH\nonly search text\n")

    with pytest.raises(SearchReplaceParseError, match="is empty"):
        _parse_search_replace_blocks("<<<<<<< SEARCH\n=======\nreplace\n>>>>>>> REPLACE")

    with pytest.raises(SearchReplaceParseError, match="missing the >>>>>>> REPLACE marker"):
        _parse_search_replace_blocks("<<<<<<< SEARCH\nalpha\n=======\nbeta\n")

    with pytest.raises(SearchReplaceParseError, match="no SEARCH/REPLACE blocks"):
        _parse_search_replace_blocks("not a patch block\n")


def test_parse_tolerates_fences_and_filename_noise() -> None:
    model_output = (
        "Here is the fix:\n"
        "```python\n"
        "# src/module.py\n"
        "<<<<<<< SEARCH\n"
        "alpha = 1\n"
        "=======\n"
        "alpha = 2\n"
        ">>>>>>> REPLACE\n"
        "```\n"
    )
    blocks = _parse_search_replace_blocks(model_output)
    assert blocks == [("alpha = 1", "alpha = 2")]


def test_apply_through_fenced_wrapper(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("alpha = 1\n", encoding="utf-8")
    model_output = (
        "```python\n"
        "# src/module.py\n"
        + _block("alpha = 1", "alpha = 2")
        + "\n```\n"
    )
    apply_search_replace_blocks(target, model_output)
    assert target.read_text(encoding="utf-8") == "alpha = 2\n"


def test_rejects_overly_broad_search_with_tiny_replacement(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    original = _large_module_body()
    target.write_text(original, encoding="utf-8")
    search = original
    replacement = "pass\n"

    with pytest.raises(DestructiveEditError, match="spans .* lines"):
        apply_search_replace_blocks(target, _block(search, replacement))

    assert target.read_text(encoding="utf-8") == original


def test_rejects_net_mass_deletion(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    head = "# keep\n" + "".join(f"line_{i} = {i}\n" for i in range(200))
    original = head
    target.write_text(original, encoding="utf-8")
    first_chunk = "".join(f"line_{i} = {i}\n" for i in range(100))
    second_chunk = "".join(f"line_{i} = {i}\n" for i in range(100, 200))
    model_output = "\n\n".join(
        [
            _block(first_chunk, "y = 1\n"),
            _block(second_chunk, "z = 1\n"),
        ]
    )

    with pytest.raises(DestructiveEditError):
        apply_search_replace_blocks(target, model_output)

    assert target.read_text(encoding="utf-8") == original


def test_small_targeted_edit_on_large_file_still_applies(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    original = _large_module_body()
    target.write_text(original, encoding="utf-8")

    apply_search_replace_blocks(
        target,
        _block("def compute():\n    return 0", "def compute():\n    return 1"),
    )

    updated = target.read_text(encoding="utf-8")
    assert updated != original
    assert "return 1" in updated
    assert "# filler line" in updated
