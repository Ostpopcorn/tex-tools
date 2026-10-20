"""Clean a LaTeX file: remove highlights, notes, and comment environments,
see `textools.macros`, and then comments, see `textools.comments`."""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .comments import Kept, Options, remove_comments
from .macros import MacroOptions, Unsure, _merge, remove_macros


@dataclass
class Line:
    """What happened to a line of the input."""
    #: The index of the line in the output, or None if it was removed
    out: Optional[int]
    #: The parts of the line that were removed, as (start, end) columns
    cuts: List[Tuple[int, int]]
    #: Why the line was changed, see `textools.comments.Line` and
    #: `textools.macros.Edit`, or "" if it was not changed
    reason: str = ""


@dataclass
class Cleaned:
    text: str
    #: What happened to each line of the input
    lines: List[Line]
    #: The number of lines in the output
    count: int
    #: The number of removed comments, notes, and comment environments
    comments: int
    #: The number of removed highlights
    highlights: int
    #: The kept text with a %, see `textools.comments.Kept`
    kept: List[Kept] = field(default_factory=list)
    #: The uses of macros that are kept, since they are not clear
    unsure: List[Unsure] = field(default_factory=list)
    #: Warnings and notes as (level, text)
    messages: List[Tuple[str, str]] = field(default_factory=list)


def _spans(columns):
    """The columns of the input, as (start, end) spans."""
    spans = []
    for col in columns:
        if col is None:
            continue
        if spans and spans[-1][1] == col:
            spans[-1] = (spans[-1][0], col + 1)
        else:
            spans.append((col, col + 1))
    return spans


def clean(text, options=Options(), macros=MacroOptions()):
    """Remove highlights, notes, comment environments, and comments."""
    body = text.replace("\r\n", "\n")
    if body.endswith("\n"):
        body = body[:-1]
    sizes = [len(_line) for _line in body.split("\n")]
    first = remove_macros(text, macros)
    # The line of the input of each line in between
    origin = [_i for _i, _edit in enumerate(first.lines)
              if _edit.out is not None]
    second = remove_comments(first.text, options,
                             numbers=[_i + 1 for _i in origin])
    lines = []
    for idx, edit in enumerate(first.lines):
        if edit.out is None:
            lines.append(Line(None, edit.cuts, edit.reason))
            continue
        line = second.lines[edit.out]
        if line.out is None:
            lines.append(Line(None, [(0, sizes[idx])] if sizes[idx] else [],
                              line.reason))
            continue
        cuts = edit.cuts + _spans(edit.columns[line.kept:])
        lines.append(Line(line.out, _merge(cuts), line.reason or edit.reason))
    kept = [Kept(_kept.kind, (origin[_kept.lines[0]], origin[_kept.lines[1]]),
                 _kept.name) for _kept in second.kept]
    return Cleaned(second.text, lines, second.count,
                   second.comments + first.notes, first.highlights, kept,
                   first.unsure, first.messages + second.messages)
