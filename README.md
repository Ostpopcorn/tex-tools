# TeX Tools

Tools for working with LaTeX files, in your browser.

## Web App
The web app shows your file next to the result with the current settings,
before you download it: open, drop, or paste (Ctrl+V) your `.tex` files, or a `.zip` or `.tar.gz` of your project.  It
runs Python in your browser with [Pyodide](https://pyodide.org), so your
files never leave your computer.

![Screenshot of the web app](web/screenshot.png)

* The files and the settings are in a panel on the left: the open files at
  the top, and the settings below them, each with an example.  _Close all_
  closes all files.  Hide the panel to a bar on the left to make room for
  the panes.
* Removed lines are marked red, and the removed parts of a changed line are
  struck through.  In the result, a red triangle on the edge marks where
  whole lines were removed, like git in VS Code.  Text that is kept on
  purpose, e.g., a `%` in a `verbatim` environment, is marked purple.
* Show the original, both side by side, or the result.  The fold button next
  to the views folds the unchanged lines, so that you see only what changed,
  with two lines around each change.  Click a fold to show its lines.
* Open the `.zip` of your project, e.g., from Overleaf, or the source of a
  paper from arXiv (a `.tar.gz`), and download it in the same format with
  the comments removed from all its `.tex` files.  The other files, e.g.,
  figures and the `.bib` file, and the folders stay as they are.  To keep
  the comments of a `.tex` file, choose it and check _Exclude_.  The source
  of a paper with a single file on arXiv is a gzipped `.tex` file, which is
  opened like a `.tex` file.
* The cleaned files are LaTeX files (`.tex`, `.ltx`, `.sty`, `.cls`,
  `.ins`), TikZ and PGF files (`.tikz`, `.pgf`), TikZiT styles
  (`.tikzstyles`), biblatex styles (`.bbx`, `.cbx`, `.lbx`, `.dbx`), and the
  LaTeX part of figures from Inkscape (`.pdf_tex`, `.eps_tex`, `.ps_tex`).
  `.dtx` files stay as they are, since their `%` lines are their
  documentation.
* Several files can be open at once, e.g., all the files of a paper.  Choose
  the shown file in the panel or above the pane, and download it, or all of them as a
  `.zip`.  Files keep their names, so that `\input` and `\include` still
  find them.
* Files that are not UTF-8 are read and saved as Latin-1, and line endings
  (`\n` or `\r\n`) are kept.

To run the web app locally, build it and serve it on http://localhost:8000
with
```bash
python3 web/build.py --serve
```
The build downloads Pyodide from npm once and caches it in
`~/.cache/textools-web`.  The workflow `.github/workflows/web.yml` publishes
the web app with GitHub Pages for every push to `main`.  In the settings of
the repository, select _GitHub Actions_ as the source under _Pages_ once.

## Remove Comments
Removes the comments of LaTeX files, e.g., before you upload them to arXiv,
where everybody can read them, without changing the typeset document.  The
steps follow the workflow with regular expressions:

| Step | Like replacing | with |
|------|----------------|------|
| 1. Empty comments | `(?<!\\)%.*` | `%` |
| 2. Remove comment lines | `\n *% *\n` | `\n` |
| 3. Remove extra blank lines | `\n *\n *\n` | `\n\n` |
| 4. Remove `%` after spaces | `[ \t]+%.*$` | nothing |

Step 1 keeps the `%`, since it also removes the line break after it, e.g., at
the end of a line in a macro definition.  Step 4 removes it after spaces,
since the spaces end the word like the line break does.

Unlike the regular expressions, the steps work on the comments as LaTeX reads
them:
* A `%` after `\\` (a line break) is a comment, `\%` is not.
* Several comment lines in a row are all removed, also on the first line and
  when they are indented with tabs.
* Step 4 only removes a `%` after text on the same line.  It never leaves an
  empty line, which would end the paragraph, or be an error in display math
  like `\[ ... \]`.  It keeps the spaces after a control space `\ `.
* Verbatim text is kept as it is: the environments `verbatim`, `Verbatim`,
  `lstlisting`, `minted`, `alltt`, and `filecontents`, and `\verb`,
  `\lstinline`, `\mintinline`, `\url`, and `\href`, e.g.,
  `\url{https://example.org/a%20b}`.  So are magic comments like
  `% !TEX program = xelatex`.

In Python:
```python
from textools import Options, remove_comments

result = remove_comments(text)
print(result.text)
# only steps 1 and 2
result = remove_comments(text, Options(blank_lines=False, space_before=False))
```

Two more settings remove what LaTeX skips or what is only for you:
* Comment environments of the `comment` package, `\begin{comment}` …
  `\end{comment}`, on lines of their own (on by default).  They are kept if
  your files define their own `comment` environment, which may show its
  text.
* Notes with their text, e.g., `text \comment{check this} more` → `text more`
  (off by default).  You choose the macros, e.g., `\comment` and `\todo`.

## Remove Highlights
Removes the highlights of changes, but keeps their text, e.g.,
`\blue{new text}` → `new text` (off by default).  You choose the macros,
e.g., `\blue` and `\red`, and the web app suggests the macros that your
files define to color their text, e.g.,
`\newcommand{\hl}[1]{\textcolor{orange}{#1}}`.  It can also accept the
changes of the `changes` package: `\added{new}` → `new`, `\deleted{old}` →
nothing, and `\replaced{new}{old}` → `new`.

Highlights and notes are read like LaTeX reads them, and it plays it safe:
* Only `\blue{…}` with its braces is a highlight: not `\bluebox`, not a
  `\blue` in a comment, in verbatim text, or in a URL, and not the definition
  of `\blue`, which stays, so that the highlights that are kept still work.
* The braces stay if they are needed, e.g., `\blue{\bfseries new}` →
  `{\bfseries new}`, so that the bold text ends where it did, and
  `\LaTeX\blue{s}` → `\LaTeX{s}`, so that the name of the command stays.
* No new paragraphs: a line that is empty after removing a note is removed.
  The words around a note keep their space, or stay together with a `%`,
  e.g., `word\comment{a⏎b}s` → `word%⏎s`.  Lines are never joined.
* What is not clear is kept as it is, with a warning that says why, e.g., a
  highlight without a closing `}`, with an optional argument `[…]`, with
  `\verb` in it, or with another `{…}` right after it, like
  `\textcolor{blue}{…}`, which may have more than one argument.

In Python, `clean` removes highlights and notes, and then comments:
```python
from textools import MacroOptions, Options, clean

result = clean(text, Options(), MacroOptions(highlights=("blue", "red"),
                                             notes=("comment",)))
print(result.text)
for unsure in result.unsure:
    print(unsure.lines, unsure.name, unsure.reason)
```

## Tests
```bash
pip install -e ".[test]"
pytest
```
