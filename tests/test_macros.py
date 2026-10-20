import pytest

from textools import MacroOptions, Options, clean, remove_macros
from textools.macros import defines_comment_env, find_highlights

ALL = MacroOptions(highlights=("blue", "red"), notes=("comment", "todo"),
                   changes=True)


def _clean(text, options=ALL):
    return remove_macros(text, options).text


@pytest.mark.parametrize("text, expected", [
    ("a \\blue{new} b", "a new b"),
    ("\\blue{a \\red{b} c}", "a b c"),
    ("\\blue{see \\cite{x} and $\\alpha$}", "see \\cite{x} and $\\alpha$"),
    ("  \\blue{x} indented", "  x indented"),
    ("a\n\\blue{\nnew\n}\nb", "a\nnew\nb"),
    # the } in the comment is not the end
    ("\\blue{a % }\n b} c", "a % }\n b c"),
    ("\\blue {spaces}", "spaces"),
])
def test_highlights_keep_their_text(text, expected):
    assert _clean(text) == expected


@pytest.mark.parametrize("text, expected", [
    # a declaration would apply to the text after it
    ("\\blue{\\bfseries new} b", "{\\bfseries new} b"),
    ("\\blue{\\color{red} new} b", "{\\color{red} new} b"),
    # a command name would run into the text next to it
    ("\\LaTeX\\blue{s} x", "\\LaTeX{s} x"),
    ("\\blue{\\LaTeX} is", "{\\LaTeX} is"),
    # a superscript of more than one character
    ("$x^\\blue{23}$", "$x^{23}$"),
])
def test_highlights_keep_braces_when_needed(text, expected):
    assert _clean(text) == expected


@pytest.mark.parametrize("text", [
    "\\newcommand{\\blue}[1]{\\textcolor{blue}{#1}}",
    "\\newcommand{\\added}[1]{%\n  \\blue{#1}}",
    "\\let\\blue\\relax",
    "\\bluebox{x}",
    "% \\blue{x}",
    "\\verb|\\blue{x}|",
    "\\begin{verbatim}\n\\blue{x}\n\\end{verbatim}",
    "\\url{https://example.com/\\blue}",
])
def test_definitions_comments_and_verbatim_stay(text):
    result = remove_macros(text, ALL)
    assert result.text == text
    assert not result.unsure and not result.messages


@pytest.mark.parametrize("text, reason", [
    ("\\blue x", "it is not followed by {...}"),
    ("\\blue[o]{y}", "it has an optional argument in [...], which is not "
                     "supported"),
    ("\\blue{open", "it has no closing }"),
    ("\\blue{\\verb|}|}", "it contains \\verb"),
    ("\\blue{\\begin{verbatim}\n}\n\\end{verbatim}}",
     "it contains a \\begin{verbatim}"),
    ("\\replaced{new} old", "it is not followed by {new}{old}"),
    ("\\blue{a} {b}", "it is followed by another {...}, so it may have "
                      "more than one argument"),
    ("\\comment{a}{b}", "it is followed by another {...}, so it may have "
                        "more than one argument"),
])
def test_unclear_uses_are_kept_with_why(text, reason):
    result = remove_macros(text, ALL)
    assert result.text == text
    assert [_unsure.reason for _unsure in result.unsure] == [reason]
    assert result.messages[0][0] == "warning"
    assert reason in result.messages[0][1]


@pytest.mark.parametrize("text, expected", [
    ("text \\comment{note} more", "text more"),
    ("prev\n\\comment{note}\nnext", "prev\nnext"),
    ("text \\comment{note}\nnext", "text\nnext"),
    ("\\comment{note} text", "text"),
    ("word\\comment{x}s", "words"),
    # a command name keeps the space after the note
    ("\\LaTeX\\comment{x} is", "\\LaTeX{} is"),
    ("\\LaTeX \\comment{y} so", "\\LaTeX {} so"),
    # over several lines, without joining them
    ("text \\comment{a\nb} more", "text\nmore"),
    ("word\\comment{a\nb}s", "word%\ns"),
    ("\\LaTeX\\comment{a\nb}s", "\\LaTeX{}%\ns"),
    ("x \\comment{a} \\comment{b}\n\\comment{a} \\comment{b}\nend",
     "x\nend"),
    ("\\todo[inline, caption={a}]{b} after", "after"),
])
def test_notes_are_removed_with_their_text(text, expected):
    assert _clean(text) == expected


def test_macros_with_more_arguments_are_kept():
    text = "\\textcolor{blue}{x}"
    result = remove_macros(text, MacroOptions(highlights=("textcolor",)))
    assert result.text == text and len(result.unsure) == 1


def test_notes_never_leave_a_blank_line():
    text = "one\n  \\comment{a\n\nb}  \ntwo\n"
    result = remove_macros(text, ALL)
    assert result.text == "one\ntwo\n"
    assert [_edit.out for _edit in result.lines] == [0, None, None, None, 1]
    assert result.notes == 1


def test_changes():
    text = ("\\added[id=A]{new} \\deleted{old} \\replaced{now}{then} "
            "\\replaced[id=B]{a} {b} \\highlight{c} end")
    result = remove_macros(text, MacroOptions(changes=True))
    assert result.text == "new now a c end"
    assert result.highlights == 5
    assert _clean("\\replaced{\\bfseries x}{y}z") == "{\\bfseries x}z"
    # without changes, they stay
    assert remove_macros(text).text == text


def test_comment_envs():
    text = "p\n\\begin{comment}\n\\blue{ unbalanced\n\\end{comment}\nq\n"
    result = remove_macros(text, ALL)
    assert result.text == "p\nq\n"
    assert [_edit.reason for _edit in result.lines[1:4]] == [
        "comment environment"] * 3
    assert remove_macros(text, MacroOptions(comment_envs=False)).text == text


@pytest.mark.parametrize("text, reason", [
    ("x \\begin{comment} y \\end{comment}", "it is not on lines of its own"),
    ("\\begin{comment}\nno end", "it has no \\end{comment}"),
])
def test_unclear_comment_envs_are_kept(text, reason):
    result = remove_macros(text)
    assert result.text == text
    assert [_unsure.reason for _unsure in result.unsure] == [reason]


def test_comment_envs_of_ones_own():
    assert defines_comment_env("\\renewenvironment{comment}{}{}")
    assert defines_comment_env("\\includecomment{comment}")
    assert not defines_comment_env("\\excludecomment{note}")
    text = "\\begin{comment}\nx\n\\end{comment}\n"
    result = remove_macros(text, MacroOptions(own_comment_env=True))
    assert result.text == text
    assert result.messages == [("info", "Kept the comment environments, "
                                "since your files define their own.")]


def test_lines_and_cuts():
    result = remove_macros("a \\blue{b}\n\\LaTeX\\comment{x}s\r\n", ALL)
    assert result.text == "a b\r\n\\LaTeX{}s\r\n"
    first, second = result.lines
    assert (first.out, first.cuts, first.reason) == (0, [(2, 8), (9, 10)],
                                                     "highlight")
    assert (second.out, second.cuts, second.reason) == (1, [(6, 17)], "note")
    # the inserted {} has no column in the input
    assert second.columns == list(range(6)) + [None, None, 17]


def test_idempotent():
    text = ("\\blue{a} \\comment{b} \\LaTeX\\blue{s}\n\\replaced{x}{y}\n"
            "\\begin{comment}\nz\n\\end{comment}\n\\blue{\\bfseries q}\n")
    once = _clean(text)
    assert _clean(once) == once


def test_find_highlights():
    text = ("\\newcommand{\\hl}[1]{\\textcolor{orange}{#1}}\n"
            "\\newcommand\\new[1]{{\\color{blue}#1}}\n"
            "\\def\\old#1{\\textcolor{red}{#1}}\n"
            "\\NewDocumentCommand{\\mark}{m}{\\textcolor{red}{#1}}\n"
            "\\newcommand{\\plain}[1]{\\textbf{#1}}\n"
            "\\newcommand{\\colorbox}{x}\n")
    assert find_highlights(text) == ["hl", "new", "old", "mark"]


def test_clean_composes_with_comments():
    text = "A \\blue{new} word. % x\n\\comment{a}\n% y\nB\n"
    result = clean(text, Options(), ALL)
    assert result.text == "A new word.\nB\n"
    assert [(_line.out, _line.cuts, _line.reason) for _line in result.lines] == [
        (0, [(2, 8), (11, 12), (18, 22)], "comment"),
        (None, [(0, 11)], "note"),
        (None, [(0, 3)], "comment line"),
        (1, [], "")]
    assert (result.comments, result.highlights) == (3, 1)


def test_clean_names_the_lines_of_the_input():
    text = "\\comment{a}\n\\begin{verbatim}\n"
    result = clean(text, Options(), ALL)
    assert result.messages == [("warning", "\\begin{verbatim} in line 2 has no "
                                "\\end{verbatim}, so the rest of the file is "
                                "kept as it is.")]
