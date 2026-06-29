from click._compat import strip_ansi
from click.formatting import wrap_text


def test_haao_r102_ansi_styled_long_word_wraps_without_blank_lines():
    styled_body = "\x1b[31mabcdefghij\x1b[0m"

    styled = wrap_text(styled_body, width=5)

    assert [strip_ansi(line) for line in styled.splitlines()] == ["abcde", "fghij"]
    assert styled.count("\x1b[31m") == 1
    assert styled.count("\x1b[0m") == 1
