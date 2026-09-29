r"""Remove the comments of LaTeX files without changing the document.

The steps follow the workflow with regular expressions:

1. Empty comments: ``(?<!\\)%.*`` → ``%``
2. Remove comment lines: ``\n *% *\n`` → ``\n``
3. Remove extra blank lines: ``\n *\n *\n`` → ``\n\n``
4. Remove ``%`` after text: ``[ \t]+%.*$`` → nothing

Step 1 keeps the ``%``, since it also removes the line break after it, e.g.,
at the end of a line in a macro definition. The steps work line by line on
the comments as LaTeX reads them, so that, unlike the regular expressions:

* a ``%`` after ``\\`` (a line break) is a comment, while ``\%`` is not,
* all consecutive comment lines are removed, also on the first line,
* step 4 only removes ``%`` after text on the same line, so that it never
  leaves an empty line, which would end the paragraph or be an error in
  display math like ``\[ ... \]``,
* verbatim text, e.g., ``\verb|%|`` and ``lstlisting`` environments, URLs,
  e.g., ``\url{a%20b}``, and magic comments like ``% !TEX program = xelatex``
  are kept as they are.
"""
import bisect
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Tuple

# Environments whose content LaTeX reads verbatim, where % is not a comment
VERBATIM_ENVS = ("verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim",
                 "LVerbatim", "lstlisting", "minted", "alltt", "filecontents",
                 "filecontents*")

# \\ and \% are not the start of a comment
_ESCAPE = r"\\[\\%]"
_VERBATIM = (r"(?P<verbatim>\\begin\{(?P<env>"
             + "|".join(re.escape(_env) for _env in VERBATIM_ENVS)
             + r")\}[\s\S]*?(?:\\end\{(?P=env)\}|(?P<unclosed>\Z)))")
# \verb|...|, \lstinline|...| or {...}, \mintinline{lang}|...| or {...}
_INLINE = (r"(?P<inline>\\verb\*?(?P<d1>[^\sA-Za-z*])[^\n]*?(?:(?P=d1)|$)"
           r"|\\(?:lstinline|mintinline(?:\[[^\]\n]*\])?\{[^}\n]*\})"
           r"(?:\[[^\]\n]*\])?"
           r"(?:\{[^}\n]*\}|(?P<d2>[^\sA-Za-z{])[^\n]*?(?:(?P=d2)|$)))")
_URL = r"(?P<url>\\(?:url|href)\{[^{}\n]*\})"
_MAGIC = r"(?P<magic>^[ \t]*%[ \t]*!(?i:tex|bib)\b[^\n]*)"
_COMMENT = r"(?P<comment>%[^\n]*)"


@lru_cache(maxsize=None)
def _scanner(keep_verbatim, keep_magic):
    parts = [_ESCAPE]
    if keep_verbatim:
        parts += [_VERBATIM, _INLINE, _URL]
    if keep_magic:
        parts.append(_MAGIC)
    parts.append(_COMMENT)
    return re.compile("|".join(parts), re.MULTILINE)


@dataclass(frozen=True)
class Options:
    """The steps to remove comments and what to keep as it is."""
    #: 1. Remove the text of comments, but keep the ``%``
    empty_comments: bool = True
    #: 2. Remove lines with only a ``%``
    comment_lines: bool = True
    #: 3. Replace two or more blank lines by one
    blank_lines: bool = True
    #: 4. Remove spaces and a ``%`` after text, which a line break replaces
    space_before: bool = True
    #: Keep ``%`` in verbatim environments, ``\verb`` and URLs
    keep_verbatim: bool = True
    #: Keep magic comments like ``% !TEX program = xelatex``
    keep_magic: bool = True


@dataclass
class Line:
    """What happened to a line of the input."""
    #: The index of the line in the output, or None if it was removed
    out: Optional[int]
    #: The number of characters kept at the start of the line
    kept: int
    #: Why the line was changed or removed: "comment", "comment line",
    #: "blank line", or "" if it was not changed
    reason: str = ""


@dataclass
class Kept:
    """Text with a % that was kept, since it is not a comment."""
    #: "verbatim", "inline", "url", or "magic"
    kind: str
    #: The first and last line (starting at 0)
    lines: Tuple[int, int]
    #: The environment or command, e.g., "lstlisting" or "\\url"
    name: str


@dataclass
class Result:
    text: str
    #: What happened to each line of the input
    lines: List[Line]
    #: The number of lines in the output
    count: int
    #: The number of comments that were emptied or removed
    comments: int
    kept: List[Kept] = field(default_factory=list)
    #: Warnings and notes as (level, text)
    messages: List[Tuple[str, str]] = field(default_factory=list)


def _scan(body, options):
    """Return the column of the comment in each line, the lines that are
    kept as they are, the kept text with a %, and warnings."""
    starts = [0] + [_m.end() for _m in re.finditer("\n", body)]
    line_of = lambda idx: bisect.bisect_right(starts, idx) - 1
    comments = {}
    fixed = set()
    kept = []
    messages = []
    scanner = _scanner(options.keep_verbatim, options.keep_magic)
    for match in scanner.finditer(body):
        kind = match.lastgroup
        if kind is None:
            continue
        first = line_of(match.start())
        if kind == "comment":
            comments[first] = match.start() - starts[first]
            continue
        last = line_of(max(match.start(), match.end() - 1))
        if kind in ("verbatim", "magic"):
            fixed.update(range(first, last + 1))
        if kind == "verbatim":
            name = match.group("env")
            if match.group("unclosed") is not None:
                messages.append(("warning", "\\begin{{{0}}} in line {1} has no "
                                 "\\end{{{0}}}, so the rest of the file is kept "
                                 "as it is.".format(name, first + 1)))
        elif kind == "magic":
            name = "% !" + match.group().split("!", 1)[1][:3]
        else:
            name = re.match(r"\\[A-Za-z]+", match.group()).group()
        if "%" in match.group():
            kept.append(Kept(kind, (first, last), name))
    return comments, fixed, kept, messages


def _blank(line):
    return not line.strip(" \t")


def remove_comments(text, options=Options()):
    """Remove the comments of a LaTeX file, see the steps in `Options`."""
    eol = "\r\n" if "\r\n" in text else "\n"
    body = text.replace("\r\n", "\n")
    final_newline = body.endswith("\n")
    if final_newline:
        body = body[:-1]
    comments, fixed, kept, messages = _scan(body, options)

    out = []
    lines = []
    count = 0
    previous_blank = False
    for idx, line in enumerate(body.split("\n")):
        col = comments.get(idx)
        new, reason = line, ""
        if col is not None:
            # 1. Remove the text of the comment
            if options.empty_comments and len(line) > col + 1:
                new, reason = line[:col+1], "comment"
            # 2. Remove a line with only a %
            if (options.comment_lines and idx not in fixed
                    and new.strip(" \t") == "%"):
                lines.append(Line(None, 0, "comment line"))
                count += 1
                continue
            # 4. Remove the spaces and the % after text. The spaces are one
            # space, like the line break without the %. Spaces after a
            # backslash are kept, since the first one is a control space.
            before = new[:col].rstrip(" \t")
            trailing_backslashes = len(before) - len(before.rstrip("\\"))
            if (options.space_before and len(before) < col
                    and not _blank(before) and trailing_backslashes % 2 == 0):
                new, reason = before, "comment"
            if reason:
                count += 1
        # 3. Remove a blank line after a blank line
        blank = _blank(new) and idx not in fixed
        if options.blank_lines and blank and previous_blank:
            lines.append(Line(None, 0, "blank line"))
            continue
        previous_blank = blank
        lines.append(Line(len(out), len(new), reason))
        out.append(new)

    result = eol.join(out) + (eol if final_newline and out else "")
    _note_kept(kept, messages)
    return Result(result, lines, len(out), count, kept, messages)


def _note_kept(kept, messages):
    """Add a note on the kept text with a %."""
    names = {"verbatim": ("verbatim environment", "verbatim environments"),
             "inline": ("inline verbatim", "inline verbatims"),
             "url": ("URL", "URLs"),
             "magic": ("magic comment", "magic comments")}
    parts = []
    for kind, (one, many) in names.items():
        number = sum(1 for _kept in kept if _kept.kind == kind)
        if number:
            parts.append("{} {}".format(number, one if number == 1 else many))
    if len(parts) > 1:
        parts[-2:] = [" and ".join(parts[-2:])]
    if parts:
        messages.append(("info", "Kept the % in {}.".format(", ".join(parts))))
