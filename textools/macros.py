r"""Remove highlights of changes, e.g., ``\blue{new text}``, notes, e.g.,
``\comment{check this}``, and the environments of the comment package.

Like removing comments, this plays it safe: what is not clear is kept as it
is, with a note that says why. The text is read like LaTeX reads it, so
that a macro in a comment, in verbatim text, or in a definition is not a
use of it. A highlight keeps its text, e.g., ``\blue{new}`` → ``new``, and a
note is removed with its text, so that

* no blank line is left, which would start a new paragraph,
* the words around a note keep their space, or stay together,
* the braces of a highlight are kept if they hold a declaration, e.g.,
  ``\blue{\bfseries new}`` → ``{\bfseries new}``, or if a command name
  would run into the text next to it, e.g., ``\LaTeX\blue{s}``.

Lines are never joined: a line is changed, removed, or kept, so that each
line of the result is a line of the input.
"""
import bisect
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .comments import VERBATIM_ENVS, _INLINE, _URL

#: The macros of the changes package: the text of \added and \highlight is
#: kept, \deleted is removed with its text, and \replaced{new}{old} keeps
#: the new text
CHANGES = {"added": "keep", "highlight": "keep", "deleted": "drop",
           "replaced": "replace"}

# Environments whose content is not read, including the comment
# environment of the comment package
_SKIPPED_ENVS = VERBATIM_ENVS + ("comment",)
_TOKEN = re.compile(
    # control symbols, e.g., \\, \%, \{, and \}
    r"(?P<escape>\\[^A-Za-z])"
    r"|(?P<env>\\begin\{(?P<envname>"
    + "|".join(re.escape(_env) for _env in _SKIPPED_ENVS)
    + r")\}[\s\S]*?(?:\\end\{(?P=envname)\}|(?P<unclosed>\Z)))"
    r"|" + _INLINE + "|" + _URL +
    r"|(?P<comment>%[^\n]*)"
    r"|(?P<word>\\[A-Za-z]+)"
    r"|(?P<open>\{)|(?P<close>\})")
# A line that defines a macro, e.g., \newcommand{\blue}[1]{...}, which is
# not a use of it
_DEFINES = re.compile(
    r"\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand"
    r"|(?:New|Renew|Provide|Declare)DocumentCommand|[gex]?def|let)(?![A-Za-z])")
_CONTROL_WORD_END = re.compile(r"\\[A-Za-z]+$")
# A # that is not \#, which is a parameter in a definition
_PARAMETER = re.compile(r"(?<!\\)#")
# Math, where declarations stay inside
_MATH = re.compile(r"(?<!\\)\$\$?[\s\S]*?(?<!\\)\$\$?|\\\([\s\S]*?\\\)"
                   r"|\\\[[\s\S]*?\\\]")
# Commands with an argument that apply to the text after them, e.g.,
# \color{red}
_DECLARATIONS = {"color", "pagecolor", "fontsize", "fontfamily", "fontseries",
                 "fontshape", "setlength", "addtolength", "setcounter",
                 "addtocounter", "stepcounter", "definecolor", "linespread",
                 "newcommand", "renewcommand", "def", "let", "usefont"}
# The definitions of a comment environment of one's own, which may show
# its text, so that the comment environments are kept
_OWN_COMMENT_ENV = re.compile(
    r"\\(?:re)?newenvironment\*?\s*\{comment\}"
    r"|\\(?:New|Renew|Provide|Declare)DocumentEnvironment\s*\{comment\}"
    r"|\\(?:includecomment|specialcomment|processcomment)\s*\{comment\}")
# The definition of a macro that colors its argument, e.g.,
# \newcommand{\hl}[1]{\textcolor{orange}{#1}}, for suggestions
_COLOR_MACRO = re.compile(
    r"\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand)\*?\s*"
    r"\{?\s*\\([A-Za-z]+)\s*\}?\s*\[1\]\s*\{([^\n]*)"
    r"|\\(?:New|Renew|Provide|Declare)DocumentCommand\s*\{?\s*\\([A-Za-z]+)"
    r"\s*\}?\s*\{\s*m\s*\}\s*\{([^\n]*)"
    r"|\\[gex]?def\s*\\([A-Za-z]+)\s*#1\s*\{([^\n]*)")


@dataclass(frozen=True)
class MacroOptions:
    """The macros and environments to remove."""
    #: Remove the comment environments of the comment package
    comment_envs: bool = True
    #: Remove these macros with their text, e.g., ("comment",)
    notes: Tuple[str, ...] = ()
    #: Remove these macros, but keep their text, e.g., ("blue", "red")
    highlights: Tuple[str, ...] = ()
    #: Accept the changes of the changes package, see `CHANGES`
    changes: bool = False
    #: The files define their own comment environment, see
    #: `defines_comment_env`, so that the comment environments are kept
    own_comment_env: bool = False


@dataclass
class Edit:
    """What happened to a line of the input."""
    #: The index of the line in the output, or None if it was removed
    out: Optional[int]
    #: The parts of the line that were removed, as (start, end) columns
    cuts: List[Tuple[int, int]]
    #: Why the line was changed: "highlight", "note", "deleted", or
    #: "comment environment", or "" if it was not changed
    reason: str = ""
    #: For each character of the output line, its column in the input, or
    #: None for an inserted character, e.g., the % that keeps two words
    #: together
    columns: List[Optional[int]] = field(default_factory=list, repr=False)


@dataclass
class Unsure:
    """A use of a macro or an environment that is kept, since it is not
    clear how to remove it."""
    #: The first and last line (starting at 0)
    lines: Tuple[int, int]
    #: The macro or environment, e.g., "\\blue"
    name: str
    #: Why it is kept
    reason: str


@dataclass
class MacroResult:
    text: str
    #: What happened to each line of the input
    lines: List[Edit]
    #: The number of lines in the output
    count: int
    #: The number of removed highlights, including the accepted changes
    highlights: int = 0
    #: The number of removed notes and comment environments
    notes: int = 0
    unsure: List[Unsure] = field(default_factory=list)
    #: Notes as (level, text)
    messages: List[Tuple[str, str]] = field(default_factory=list)
    #: Whether the text has comment environments
    envs: bool = False


class _Unclear(Exception):
    """Why a use is not clear."""


def defines_comment_env(text):
    """Whether a file defines its own comment environment, which may show
    its content."""
    return bool(_OWN_COMMENT_ENV.search(text))


def find_highlights(text):
    """The names of the macros that color their argument, e.g., "hl" for
    \\newcommand{\\hl}[1]{\\textcolor{orange}{#1}}, in the order of their
    definitions."""
    names = []
    for match in _COLOR_MACRO.finditer(text):
        name = match.group(1) or match.group(3) or match.group(5)
        body = match.group(2) or match.group(4) or match.group(6) or ""
        if ("#1" in body and re.search(r"\\(?:text)?color(?![A-Za-z])", body)
                and name not in names):
            names.append(name)
    return names


def _blank(text):
    return not text.strip(" \t")


class _Lines:
    """The changes of the lines, collected before they are made."""

    def __init__(self, body):
        self.body = body
        self.lines = body.split("\n")
        self.starts = [0]
        for line in self.lines[:-1]:
            self.starts.append(self.starts[-1] + len(line) + 1)
        self.cuts: Dict[int, List[Tuple[int, int]]] = {}
        self.inserts: Dict[int, List[Tuple[int, str]]] = {}
        self.removed: Dict[int, str] = {}
        self.reasons: Dict[int, str] = {}

    def line_of(self, pos):
        return bisect.bisect_right(self.starts, pos) - 1

    def cut(self, line, start, end, reason):
        if end > start:
            self.cuts.setdefault(line, []).append((start, end))
            self.reasons.setdefault(line, reason)

    def insert(self, line, col, text, reason):
        self.inserts.setdefault(line, []).append((col, text))
        self.reasons.setdefault(line, reason)

    def remove(self, first, last, reason):
        for line in range(first, last + 1):
            self.removed.setdefault(line, reason)

    def cut_span(self, start, end, reason):
        """Remove a part of a line, e.g., \\blue{ of a highlight."""
        line = self.line_of(start)
        self.cut(line, start - self.starts[line], end - self.starts[line],
                 reason)

    def drop(self, start, end, reason):
        """Remove a note with its text, so that the text around it reads
        the same, without joining lines."""
        a, b = self.line_of(start), self.line_of(end)
        sa, eb = start - self.starts[a], end - self.starts[b]
        before, after = self.lines[a][:sa], self.lines[b][eb:]
        ws_before = len(before) - len(before.rstrip(" \t"))
        ws_after = len(after) - len(after.lstrip(" \t"))
        # A command name before the note, e.g., \LaTeX, takes the spaces
        # after it, and would take the letters after it, so a {} ends it
        command = bool(_CONTROL_WORD_END.search(before.rstrip(" \t")))
        if command:
            self.insert(a, sa, "{}", reason)
        # The spaces after a command name are not a space in the text
        space_before = ws_before > 0 and not command
        glue = (not _blank(before) and not _blank(after) and not space_before
                and not ws_after)
        if a == b:
            if command:
                self.cut(a, sa, eb, reason)
            elif _blank(before):
                self.cut(a, sa, eb + ws_after, reason)
            elif _blank(after):
                self.cut(a, sa - ws_before, eb, reason)
            elif space_before and ws_after:
                self.cut(a, sa, eb + ws_after, reason)
            else:
                self.cut(a, sa, eb, reason)
            return
        # Over several lines: the end of the first line, the lines between,
        # and the start of the last line. Without a space around the note,
        # a % keeps the words together.
        self.cut(a, sa - (ws_before if space_before else 0),
                 len(self.lines[a]), reason)
        if glue:
            self.insert(a, sa, "%", reason)
        self.remove(a + 1, b - 1, reason)
        # The spaces at the start of a line are not read
        self.cut(b, 0, eb + ws_after, reason)

    def apply(self):
        """The lines of the output, and what happened to each line."""
        out = []
        edits = []
        for idx, line in enumerate(self.lines):
            if idx in self.removed:
                edits.append(Edit(None, [(0, len(line))] if line else [],
                                  self.removed[idx]))
                continue
            cuts = _merge(self.cuts.get(idx, []))
            inserts = sorted(self.inserts.get(idx, []), key=lambda i: i[0])
            new, columns = _apply(line, cuts, inserts)
            reason = self.reasons.get(idx, "")
            # The spaces left at the end of a changed line, which TeX
            # removes from each line anyway
            stripped = len(new.rstrip(" \t"))
            if reason and stripped < len(new):
                cuts = _merge(cuts + [(_c, _c + 1) for _c in columns[stripped:]
                                      if _c is not None])
                new, columns = new[:stripped], columns[:stripped]
            # A line that is blank now would start a new paragraph
            if reason and _blank(new) and not _blank(line):
                edits.append(Edit(None, [(0, len(line))], reason))
                continue
            edits.append(Edit(len(out), cuts, reason, columns))
            out.append(new)
        return out, edits


def _merge(cuts):
    merged = []
    for start, end in sorted(cuts):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _apply(line, cuts, inserts):
    """The line without the cuts and with the inserted text, and the column
    in the line of each character."""
    new = []
    columns = []
    cut = iter(cuts)
    current = next(cut, None)
    ins = 0
    for col in range(len(line) + 1):
        while ins < len(inserts) and inserts[ins][0] == col:
            new.append(inserts[ins][1])
            columns.extend([None] * len(inserts[ins][1]))
            ins += 1
        if col == len(line):
            break
        while current is not None and col >= current[1]:
            current = next(cut, None)
        if current is not None and current[0] <= col < current[1]:
            continue
        new.append(line[col])
        columns.append(col)
    return "".join(new), columns


def _group_end(body, start):
    """The end of the group that starts with the { at start."""
    depth = 0
    for match in _TOKEN.finditer(body, start):
        kind = match.lastgroup
        if kind == "open":
            depth += 1
        elif kind == "close":
            depth -= 1
            if depth == 0:
                return match.end()
        elif kind == "env":
            raise _Unclear("it contains a \\begin{{{}}}"
                           .format(match.group("envname")))
        elif kind == "inline":
            raise _Unclear("it contains {}".format(
                re.match(r"\\[A-Za-z]+", match.group()).group()))
    raise _Unclear("it has no closing }")


def _option_end(body, start):
    """The end of the optional argument [...] at start, on the same line."""
    depth = 0
    for pos in range(start + 1, len(body)):
        char = body[pos]
        if char == "\n":
            break
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "]" and depth == 0:
            return pos + 1
    raise _Unclear("its optional argument has no ]")


def _skip_spaces(body, pos):
    while pos < len(body) and body[pos] in " \t":
        pos += 1
    return pos


def _needs_braces(body, start, content, after):
    """Whether a highlight keeps its braces: if a declaration in it would
    apply to the text after it, or if a command name would run into the
    text next to it."""
    before = body[max(0, start - 100):start]
    last = before.rstrip(" \t")[-1:]
    if _CONTROL_WORD_END.search(before) or (last and last in "^_"):
        # e.g., \LaTeX\blue{s}, or x^\blue{2}
        return True
    text = body[content[0]:content[1]]
    if re.search(r"\\[A-Za-z]+\s*$", text):
        # e.g., \blue{\LaTeX} is
        return True
    for match in re.finditer(r"\\([A-Za-z]+)\*?\s*(.?)", _MATH.sub("", text)):
        # e.g., \bfseries, or \color{red}
        if match.group(1) in _DECLARATIONS or match.group(2) not in ("{", "["):
            return True
    return False


def remove_macros(text, options=MacroOptions()):
    """Remove highlights, notes, and comment environments, see
    `MacroOptions`."""
    eol = "\r\n" if "\r\n" in text else "\n"
    body = text.replace("\r\n", "\n")
    final_newline = body.endswith("\n")
    if final_newline:
        body = body[:-1]
    kinds = {_name: "keep" for _name in options.highlights}
    if options.changes:
        kinds.update(CHANGES)
    kinds.update({_name: "drop" for _name in options.notes})
    lines = _Lines(body)
    result = MacroResult("", [], 0)
    unsure = result.unsure
    # Text that is removed, which is not read for macros
    dropped: List[Tuple[int, int]] = []

    def keep_as_is(start, end, name, reason):
        unsure.append(Unsure((lines.line_of(start), lines.line_of(end)),
                             name, reason))

    pos = 0
    while True:
        match = _TOKEN.search(body, pos)
        if match is None:
            break
        start = match.start()
        idx = bisect.bisect_right(dropped, (start, len(body))) - 1
        if idx >= 0 and dropped[idx][0] <= start < dropped[idx][1]:
            pos = dropped[idx][1]
            continue
        pos = match.end()
        kind = match.lastgroup
        if kind == "env" and match.group("envname") == "comment":
            result.envs = True
            if options.comment_envs and not options.own_comment_env:
                _drop_env(body, lines, match, result, keep_as_is)
            continue
        if kind != "word" or match.group()[1:] not in kinds:
            continue
        name = match.group()[1:]
        action = kinds[name]
        line = lines.line_of(start)
        if _DEFINES.search(body[lines.starts[line]:start]):
            continue
        try:
            arg = _skip_spaces(body, pos)
            if arg < len(body) and body[arg] == "[":
                if action == "keep" and name not in CHANGES:
                    raise _Unclear("it has an optional argument in [...], "
                                   "which is not supported")
                arg = _skip_spaces(body, _option_end(body, arg))
            if arg >= len(body) or body[arg] != "{":
                raise _Unclear("it is not followed by {...}")
            end = _group_end(body, arg)
            if action == "replace":
                old = _skip_spaces(body, end)
                if old >= len(body) or body[old] != "{":
                    raise _Unclear("it is not followed by {new}{old}")
                old_end = _group_end(body, old)
        except _Unclear as err:
            keep_as_is(start, pos, "\\" + name, str(err))
            continue
        if _PARAMETER.search(body[arg:old_end if action == "replace" else end]):
            # a part of a definition, e.g., \blue{#1}
            continue
        if action == "drop":
            reason = "deleted" if name in CHANGES else "note"
            lines.drop(start, end, reason)
            bisect.insort(dropped, (start, end))
            if reason == "note":
                result.notes += 1
            else:
                result.highlights += 1
            pos = end
            continue
        result.highlights += 1
        content = (arg + 1, end - 1)
        follow = old_end if action == "replace" else end
        if _needs_braces(body, start, content, follow):
            lines.cut_span(start, arg, "highlight")
            if action == "replace":
                lines.drop(end, old_end, "deleted")
        else:
            lines.cut_span(start, arg + 1, "highlight")
            if action == "replace":
                lines.drop(end - 1, old_end, "deleted")
            else:
                lines.cut_span(end - 1, end, "highlight")
        if action == "replace":
            bisect.insort(dropped, (end, old_end))
        # The highlights in the text of this one
        pos = arg + 1

    out, edits = lines.apply()
    result.text = eol.join(out) + (eol if final_newline and out else "")
    result.lines = edits
    result.count = len(out)
    _note(result, options)
    return result


def _drop_env(body, lines, match, result, keep_as_is):
    """Remove a comment environment, if it is on lines of its own."""
    start, end = match.start(), match.end()
    first, last = lines.line_of(start), lines.line_of(end)
    if match.group("unclosed") is not None:
        keep_as_is(start, end, "\\begin{comment}",
                   "it has no \\end{comment}")
        return
    before = body[lines.starts[first]:start]
    after = body[end:lines.starts[last] + len(lines.lines[last])]
    if not _blank(before) or not _blank(after):
        keep_as_is(start, end, "\\begin{comment}",
                   "it is not on lines of its own")
        return
    lines.remove(first, last, "comment environment")
    result.notes += 1


def _note(result, options):
    """Say which uses were kept, and why."""
    for unsure in result.unsure[:3]:
        first, last = unsure.lines
        where = ("line {}".format(first + 1) if first == last
                 else "lines {}–{}".format(first + 1, last + 1))
        result.messages.append(("warning", "Kept {} in {}: {}.".format(
            unsure.name, where, unsure.reason)))
    if len(result.unsure) > 3:
        result.messages.append(("warning", "Kept {} more, since it is not "
                                "clear how to remove them.".format(
                                    len(result.unsure) - 3)))
    if options.comment_envs and options.own_comment_env and result.envs:
        result.messages.append(("info", "Kept the comment environments, "
                                "since your files define their own."))
