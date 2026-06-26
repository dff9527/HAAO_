from orchestrator.execution_loop import strip_code_fence


def test_strip_code_fence_removes_plain_fence() -> None:
    output = "```\nprint('hello')\n```"

    assert strip_code_fence(output) == "print('hello')"


def test_strip_code_fence_removes_filename_and_language_fence() -> None:
    output = "src/tablib/formats/_html.py\n```python\nprint('hello')\n```"

    assert strip_code_fence(output) == "print('hello')"


def test_strip_code_fence_preserves_clean_output_first_line() -> None:
    output = "from __future__ import annotations\n\nprint('hello')\n"

    assert strip_code_fence(output) == output
