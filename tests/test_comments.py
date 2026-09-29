import os
import re

import pytest

from textools import Options, remove_comments


OFF = Options(empty_comments=False, comment_lines=False, blank_lines=False,
              space_before=False)


def _clean(text, **options):
    return remove_comments(text, Options(**options)).text


def _manual(text):
    """The workflow with regular expressions, repeated until nothing
    changes, since matches of the same expression cannot overlap."""
    def repeat(pattern, repl, text):
        while True:
            new = re.sub(pattern, repl, text)
            if new == text:
                return text
            text = new
    text = re.sub(r"(?<!\\)%.*", "%", text)
    text = repeat(r"\n *% *\n", "\n", text)
    text = repeat(r"\n *\n *\n", "\n\n", text)
    return re.sub(r"[ \t]+%.*$", "", text, flags=re.MULTILINE)


def test_same_as_the_manual_workflow():
    text = ("\\section{Intro}\n"
            "Some text % a comment\n"
            "more text.% no space\n"
            "\n"
            "% a whole line\n"
            "\n"
            "  % indented\n"
            "% and another\n"
            "\n"
            "\n"
            "50\\% of it.\n"
            "\\newcommand{\\foo}{%\n"
            "  bar%\n"
            "}\n")
    expected = ("\\section{Intro}\n"
                "Some text\n"
                "more text.%\n"
                "\n"
                "50\\% of it.\n"
                "\\newcommand{\\foo}{%\n"
                "  bar%\n"
                "}\n")
    assert _clean(text) == _manual(text) == expected


def test_each_step():
    text = "a % x\n%\n\n\n\nb\n"
    assert _clean(text, comment_lines=False, blank_lines=False,
                  space_before=False) == "a %\n%\n\n\n\nb\n"
    assert _clean(text, blank_lines=False, space_before=False) == \
        "a %\n\n\n\nb\n"
    assert _clean(text, space_before=False) == "a %\n\nb\n"
    assert _clean(text) == "a\n\nb\n"


def test_nothing_changes_without_steps():
    text = "a % x\n%\n\n\n\nb"
    assert remove_comments(text, OFF).text == text


def test_escaped_percent_and_line_break():
    assert _clean("50\\% more\n") == "50\\% more\n"
    # \\ is a line break, so the % after it is a comment
    assert _clean("a \\\\% x\nb\n") == "a \\\\%\nb\n"
    assert _clean("a & b \\\\ % x\nc\n") == "a & b \\\\\nc\n"
    assert _clean("\\\\\\% x\n") == "\\\\\\% x\n"


def test_consecutive_comment_lines():
    assert _clean("% a\n% b\n  % c\n\t% d\ntext\n") == "text\n"


def test_no_empty_lines_in_display_math():
    text = "\\[\n  a = b\n  % one\n  % two\n  c = d % three\n\\]\n"
    assert _clean(text) == "\\[\n  a = b\n  c = d\n\\]\n"
    # without step 2, the % lines stay, since step 4 would leave empty lines
    assert _clean(text, comment_lines=False) == \
        "\\[\n  a = b\n  %\n  %\n  c = d\n\\]\n"


def test_control_space_is_kept():
    assert _clean("Mr.\\ % x\nSmith\n") == "Mr.\\ %\nSmith\n"


def test_verbatim_is_kept():
    text = ("\\begin{lstlisting}\n"
            "x = 5 % 3  # not a comment\n"
            "\n"
            "\n"
            "%\n"
            "\\end{lstlisting} % a comment\n")
    result = remove_comments(text)
    assert result.text == text.replace(" % a comment", "")
    assert [(k.kind, k.lines, k.name) for k in result.kept] == [
        ("verbatim", (0, 5), "lstlisting")]
    assert result.messages == [
        ("info", "Kept the % in 1 verbatim environment.")]
    assert _clean(text, keep_verbatim=False) == \
        "\\begin{lstlisting}\nx = 5\n\n\\end{lstlisting}\n"


def test_inline_verbatim_and_urls_are_kept():
    text = ("\\verb|%| \\verb*+%+ \\lstinline{%} \\url{a%20b} "
            "\\href{a%20b}{c} % x\n")
    assert _clean(text) == text.replace(" % x", "")
    result = remove_comments(text)
    assert [k.name for k in result.kept] == [
        "\\verb", "\\verb", "\\lstinline", "\\url", "\\href"]


def test_verbatim_in_a_comment_is_a_comment():
    text = "% \\begin{verbatim}\n% x\n% \\end{verbatim}\n\\verb|a| b\n"
    assert _clean(text) == "\\verb|a| b\n"


def test_unclosed_verbatim():
    result = remove_comments("\\begin{verbatim}\n% x\n")
    assert result.text == "\\begin{verbatim}\n% x\n"
    assert result.messages[0][0] == "warning"


@pytest.mark.parametrize("line", ["% !TEX program = xelatex",
                                  "%!TeX root = main.tex",
                                  "% !BIB program = biber"])
def test_magic_comments_are_kept(line):
    assert _clean(line + "\n% x\ntext\n") == line + "\ntext\n"
    assert _clean(line + "\ntext\n", keep_magic=False) == "text\n"


def test_line_endings():
    assert _clean("a % x\r\n% y\r\nb\r\n") == "a\r\nb\r\n"
    assert _clean("a % x") == "a"
    assert _clean("% only a comment\n") == ""
    assert _clean("") == ""


def test_blank_lines_in_verbatim_are_kept():
    text = "\\begin{verbatim}\n\n\n\n\\end{verbatim}\n"
    assert _clean(text) == text


def test_lines_map_to_the_output():
    text = "a % x\n% y\n\n\n\nb%\n"
    result = remove_comments(text)
    assert [(k.out, k.kept, k.reason) for k in result.lines] == [
        (0, 1, "comment"), (None, 0, "comment line"), (1, 0, ""),
        (None, 0, "blank line"), (None, 0, "blank line"), (2, 2, "")]
    assert result.count == 3 and result.comments == 2
    out = result.text.split("\n")
    for line, change in zip(text.split("\n"), result.lines):
        if change.out is not None:
            assert out[change.out] == line[:change.kept]


def test_twice_is_the_same_as_once():
    path = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "example.tex")
    with open(path, encoding="utf-8") as _file:
        text = _file.read()
    once = _clean(text)
    assert once != text
    assert _clean(once) == once
