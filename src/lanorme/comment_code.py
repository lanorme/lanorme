"""The one ``#`` comment tokeniser, and what makes a comment commented-out code.

:func:`collect_comments` reads every ``#`` comment of a source through
:mod:`tokenize` (so a ``#`` inside a string never counts); ``Module.comments``
serves it once per file per run to every check that reads comments. The
comments check then asks :func:`_looks_like_code` whether one is a disabled
statement rather than prose (CMT-001 and CMT-002). The answer is precision-first: the text must parse as a Python
statement of a kind that only code has, and every shape that parses but is
not code (a labelled note, a foreign literal, an adverb after a keyword, a
call shown with ``...``, a PEP 723 block, the lines under an ``Example:``
header) is turned away here. The restating check shares the tokeniser.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from typing import Protocol

from lanorme.markdown import URL_RE

# Comment text starting with one of these is tooling, not prose or code.
_PRAGMA_PREFIXES = (
    "noqa",
    "type:",
    "pragma",
    "pylint:",
    "mypy:",
    "ruff:",
    "isort:",
    "fmt:",
    "!",
    "-*-",
    "region",
    "endregion",
)

# Statement node types that mark a comment as commented-out code.
_CODE_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.If,
    ast.Try,
    ast.Match,
    ast.With,
    ast.AsyncWith,
    ast.Delete,
    ast.Raise,
    ast.Assert,
    ast.Return,
)


@dataclass(frozen=True)
class Comment:
    """One ``#`` comment: where it starts, its text, and whether it has a line to itself.

    *token* is the comment as written, ``#`` included; *text* is that with the
    ``#`` and surrounding whitespace stripped.
    """

    line: int
    column: int
    text: str
    standalone: bool
    token: str = ""


@dataclass(frozen=True)
class CommentScan:
    """The comments of one source, and whether the tokeniser read all of it.

    On a source :mod:`tokenize` gives up on part-way, *comments* holds the
    ones before that point and *complete* is false.
    """

    comments: tuple[Comment, ...]
    complete: bool


def collect_comments(*, source: str, source_lines: list[str]) -> CommentScan:
    """Every ``#`` comment via tokenize (so ``#`` inside strings is ignored)."""
    comments: list[Comment] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            row, col = token.start
            before = source_lines[row - 1][:col] if 0 <= row - 1 < len(source_lines) else ""
            comments.append(
                Comment(
                    line=row,
                    column=col,
                    text=token.string.lstrip("#").strip(),
                    standalone=not before.strip(),
                    token=token.string,
                ),
            )
    except (tokenize.TokenError, SyntaxError):
        return CommentScan(comments=tuple(comments), complete=False)
    return CommentScan(comments=tuple(comments), complete=True)


def _has_ellipsis_arg(*, call: ast.Call) -> bool:
    return any(isinstance(arg, ast.Constant) and arg.value is Ellipsis for arg in call.args)


# ``TODO: retries = 5`` and ``default: timeout = 30`` parse as annotated
# assignments, but their "annotation" is a plain lowercase word, which no real
# type is: a type is a builtin, a capitalised class, a subscript, an attribute,
# a union or a quoted forward reference.
_BUILTIN_TYPES = frozenset(
    {
        "int",
        "str",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "complex",
        "list",
        "dict",
        "set",
        "frozenset",
        "tuple",
        "object",
        "type",
        "range",
        "slice",
        "memoryview",
        "callable",
    },
)

# ``enabled = true`` is TOML, YAML or JavaScript quoted in a comment; Python
# spells these literals capitalised or not at all.
_FOREIGN_LITERALS = frozenset({"true", "false", "null", "nil", "undefined"})

# ``return early`` and ``import lazily`` are prose fragments that happen to
# parse: a keyword statement whose whole operand is an adverb. An ``-ly`` word
# is taken as an adverb unless it is one of the nouns that end the same way.
_ADVERBS = frozenset(
    {
        "instead",
        "anyway",
        "here",
        "there",
        "again",
        "once",
        "twice",
        "always",
        "never",
        "nothing",
        "something",
        "everything",
        "anything",
        "later",
        "soon",
        "elsewhere",
        "otherwise",
    },
)
_LY_NOUNS = frozenset(
    {
        "apply",
        "reply",
        "supply",
        "family",
        "assembly",
        "ally",
        "rally",
        "tally",
        "poly",
        "multiply",
        "imply",
        "comply",
        "anomaly",
        "monopoly",
        "jelly",
        "belly",
        "lily",
    },
)


class ModuleNames(Protocol):
    """The names the module around a comment imports and binds.

    A comment that reads like a labelled note (``created: datetime = ...``)
    or an adverb after a keyword (``return monthly``) is code after all when
    the word is one of the module's own names.
    """

    @property
    def imported(self) -> frozenset[str]:
        """Names an ``import`` statement binds."""
        ...

    @property
    def bound(self) -> frozenset[str]:
        """Every name the module binds anywhere, imports included."""
        ...


def _is_type_like(annotation: ast.expr, *, names: ModuleNames | None = None) -> bool:
    """True if an annotation could be a type rather than a note's label.

    A lowercase word is a label (``TODO: retries = 5``) unless it is a builtin
    type or a name the module imports (``from datetime import datetime``).
    """
    if isinstance(annotation, ast.Name):
        if annotation.id in _BUILTIN_TYPES or not annotation.id.islower():
            return True
        return names is not None and annotation.id in names.imported
    return True


def _is_adverb(word: str) -> bool:
    if word in _ADVERBS:
        return True
    return word.endswith("ly") and len(word) > 4 and word not in _LY_NOUNS


def _read_keyword_value(node: ast.stmt) -> ast.expr | None:
    """The expression a ``return``, ``raise``, ``yield`` or ``await`` acts on."""
    if isinstance(node, ast.Return):
        return node.value
    if isinstance(node, ast.Raise):
        return None if node.cause is not None else node.exc
    if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Yield, ast.Await)):
        return node.value.value
    return None


def _read_sole_operand(node: ast.stmt) -> str | None:
    """The bare name a keyword statement acts on, or None for anything richer."""
    if isinstance(node, ast.Import):
        alias = node.names[0] if len(node.names) == 1 else None
        return alias.name if alias is not None and alias.asname is None else None
    value = _read_keyword_value(node)
    return value.id if isinstance(value, ast.Name) else None


def _is_code_statement(node: ast.stmt, *, names: ModuleNames | None = None) -> bool:
    """True if *node* is a statement type we treat as commented-out code."""
    # 'label: type' without a value reads as documentation, not an assignment,
    # and 'label: word = value' is a labelled note unless the word is a type.
    if isinstance(node, ast.AnnAssign):
        return node.value is not None and _is_type_like(node.annotation, names=names)
    if _is_foreign_assignment(node) or _is_adverb_fragment(node, names=names):
        return False
    # 'foo(...)' with a literal ellipsis is illustrative, not dead code.
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return not _has_ellipsis_arg(call=node.value)
    return isinstance(node, _CODE_NODES)


def _is_foreign_assignment(node: ast.stmt) -> bool:
    """True for ``name = true`` and friends: another language's literal."""
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
        return False
    return node.value.id in _FOREIGN_LITERALS


def _is_adverb_fragment(node: ast.stmt, *, names: ModuleNames | None = None) -> bool:
    """True for ``return early`` and friends: a keyword and an adverb, no code.

    An ``-ly`` word the module binds itself (``monthly = ...``) is a name, so
    ``return monthly`` is a disabled statement, not a fragment.
    """
    operand = _read_sole_operand(node)
    if operand is None or not _is_adverb(operand):
        return False
    return operand in _ADVERBS or names is None or operand not in names.bound


# Block-header keywords whose comments don't parse standalone (they require a
# body). We try wrapping with ``pass`` to make them syntactically complete.
_BLOCK_HEADER_KEYWORDS = (
    "if ",
    "elif ",
    "else:",
    "for ",
    "while ",
    "try:",
    "except",
    "finally:",
    "with ",
    "match ",
    "case ",
    "def ",
    "async def ",
    "class ",
)
# Bare keyword statements that need an enclosing function to parse.
_SCOPE_BOUND_KEYWORDS = ("return", "yield", "raise", "await ")


def _list_parsing_candidates(text: str) -> list[str]:
    """Variants of *text* to try, covering Python shapes that don't parse standalone."""
    stripped = text.strip()
    candidates: list[str] = [text]
    if stripped.endswith(":") and stripped.startswith(_BLOCK_HEADER_KEYWORDS):
        candidates.append(text + "\n    pass")
    # ``try:`` alone is invalid; it needs an ``except`` or ``finally`` clause.
    if stripped == "try:" or (stripped.startswith("try ") and stripped.endswith(":")):
        candidates.append(text + "\n    pass\nexcept Exception:\n    pass")
    # ``elif`` / ``else`` and ``except`` / ``finally`` need a preceding parent.
    if stripped.startswith(("elif ", "else:")):
        candidates.append(f"if True:\n    pass\n{text}\n    pass")
    if stripped.startswith(("except", "finally:")):
        candidates.append(f"try:\n    pass\n{text}\n    pass")
    # Bare ``return`` / ``yield`` / ``raise`` / ``await`` need an enclosing def.
    first_word = stripped.split(" ", 1)[0] if stripped else ""
    if first_word in {"return", "yield", "raise"} or any(
        stripped.startswith(k) for k in _SCOPE_BOUND_KEYWORDS
    ):
        candidates.append(f"def _():\n    {text}")
    # Decorator lines (``@foo`` / ``@app.route(...)``) need a target def.
    if stripped.startswith("@"):
        candidates.append(f"{text}\ndef _():\n    pass")
    return candidates


def _comment_parses_as_code(text: str, *, names: ModuleNames | None = None) -> bool:
    """Return True if the comment text resolves to a code statement."""
    for candidate in _list_parsing_candidates(text):
        try:
            tree = ast.parse(candidate)
        except (SyntaxError, ValueError, RecursionError):
            # A deeply nested but parseable expression in a single comment can
            # overflow the parser. Treat it as prose, not commented-out code.
            continue
        for node in tree.body:
            # Unwrap the synthetic ``def _():`` used for scope-bound text and
            # decorator lines: the wrapper is a def, so it is judged by what
            # it wraps, a decorator or a code statement, never by itself.
            if isinstance(node, ast.FunctionDef) and node.name == "_":
                if node.decorator_list or any(
                    _is_code_statement(child, names=names) for child in node.body
                ):
                    return True
            elif _is_code_statement(node, names=names):
                return True
    return False


def _looks_like_code(*, text: str, names: ModuleNames | None = None) -> bool:
    """True if a comment body parses as a code statement rather than prose.

    *names* are the surrounding module's, which turn a labelled-note or
    adverb reading back into code when the word is one of them.
    """
    if not text or text.startswith(_PRAGMA_PREFIXES) or text.endswith((".", "?", "!")):
        return False
    return _comment_parses_as_code(text, names=names)


# PEP 723 inline script metadata: a ``# /// <type>`` ... ``# ///`` block whose
# inner lines are ``#`` or ``# <content>``. The body is TOML, so a line like
# ``# dependencies = ["rich"]`` parses as an assignment and would otherwise be
# flagged as commented-out code. These lines are tooling metadata, not dead code.
# This is the reference grammar from PEP 723; without the closing ``# ///`` fence
# nothing matches, so a stray opener stays lintable.
_PEP723_BLOCK = re.compile(r"(?m)^# /// [a-zA-Z0-9-]+$\s(?:^#(?: .*)?$\s)*?^# ///$")


def _find_pep723_metadata_lines(source_lines: list[str]) -> frozenset[int]:
    """Return the 1-based line numbers inside any PEP 723 inline-metadata block."""
    source = "\n".join(source_lines)
    flagged: set[int] = set()
    for match in _PEP723_BLOCK.finditer(source):
        first = source.count("\n", 0, match.start()) + 1
        flagged.update(range(first, first + match.group().count("\n") + 1))
    return frozenset(flagged)


# A comment block that opens with ``Usage:``, ``Example:`` or ``e.g.:`` goes on
# to show a call shape, not to disable one. What follows the header in the same
# block is illustration, however well it parses.
_ILLUSTRATION_HEADER = re.compile(
    r"^(?:\w+ )?(?:(?:usage|examples?|sample)\b|e\.g\.).*:$",
    re.IGNORECASE,
)


def _find_illustrative_lines(comments: list[Comment]) -> frozenset[int]:
    """Return the lines that follow an illustration header in the same block."""
    illustrative: set[int] = set()
    under_header = False
    previous = 0
    for comment in comments:
        if not comment.standalone:
            continue
        if comment.line != previous + 1:
            under_header = False
        if under_header:
            illustrative.add(comment.line)
        elif _ILLUSTRATION_HEADER.match(comment.text):
            under_header = True
        previous = comment.line
    return frozenset(illustrative)


# A licence header is as long as the licence says it is; nobody tightens the
# Apache preamble. A block carrying a licence or copyright marker is exempt from
# the block cap wherever it sits.
_LICENCE_MARKER = re.compile(r"copyright|licen[cs]e|spdx|\(c\)", re.IGNORECASE)


def _is_licence_block(block: list[Comment]) -> bool:
    return any(_LICENCE_MARKER.search(comment.text) for comment in block)


def _measure_prose_length(text: str) -> int:
    """The length a comment line is judged by: pragmas and URLs do not count.

    A pragma is written for a tool and a URL cannot be wrapped, so neither is
    something the author can tighten.
    """
    if text.startswith(_PRAGMA_PREFIXES):
        return 0
    return len(URL_RE.sub("", text))
