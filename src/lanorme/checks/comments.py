"""CMT-001 and CMT-002 (and PROSE-001 / PROSE-003 when enabled).

Selectable rules for the quality of ``#`` comments (and, for the style rules,
docstrings):

    CMT-001    No commented-out code.
    CMT-002    No verbose comments (block too long, or line too long).
    PROSE-001  No em dashes in comments or docstrings (shares the PROSE family
               with the markdown check; opt-in via ``em_dash = true``).
    PROSE-003  No emoji in comments or docstrings (shares the PROSE family;
               opt-in via ``emoji = true``).

CMT-001 and CMT-002 are hygiene and run by default. The PROSE rules stay off
until enabled::

    [tool.lanorme.comments]
    em_dash = true       # emit PROSE-001 on comments/docstrings
    emoji = true         # emit PROSE-003 on comments/docstrings
    max_block_lines = 6
    max_comment_chars = 120

CMT-005 (restating-comment detector) lives in its own ``restating`` check;
opt in via ``[tool.lanorme.restating] enabled = true``.

Run:
    lanorme check . --check=comments
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import read_int, is_flag_set
from lanorme.checks.file_limits import _measure_cyclomatic_complexity
from lanorme.sources import Module, iter_parsed_modules

_EM_DASH = "—"

_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000026ff"
    "\U00002700-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "\U00002b00-\U00002bff"
    "\U0000fe0f"
    "\U0000200d"
    "]"
)

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
class _Comment:
    line: int
    column: int
    text: str
    standalone: bool


def _collect_comments(*, source: str, source_lines: list[str]) -> list[_Comment]:
    """Return every ``#`` comment via tokenize (so ``#`` inside strings is ignored)."""
    comments: list[_Comment] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            row, col = token.start
            before = source_lines[row - 1][:col] if 0 <= row - 1 < len(source_lines) else ""
            comments.append(
                _Comment(
                    line=row,
                    column=col,
                    text=token.string.lstrip("#").strip(),
                    standalone=not before.strip(),
                )
            )
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return comments


def _collect_docstring_lines(*, module: Module) -> list[tuple[int, str]]:
    """Return (line, text) for each line of every module/class/function docstring."""
    out: list[tuple[int, str]] = []
    for node in module.index.collect(ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef):
        doc = ast.get_docstring(node, clean=False)
        if doc is None or not node.body:
            continue
        start = node.body[0].lineno
        for offset, text in enumerate(doc.splitlines()):
            out.append((start + offset, text))
    return out


def _has_ellipsis_arg(*, call: ast.Call) -> bool:
    return any(isinstance(arg, ast.Constant) and arg.value is Ellipsis for arg in call.args)


def _is_code_statement(node: ast.stmt) -> bool:
    """True if *node* is a statement type we treat as commented-out code."""
    # 'label: type' without a value reads as documentation, not an assignment.
    if isinstance(node, ast.AnnAssign) and node.value is None:
        return False
    # 'foo(...)' with a literal ellipsis is illustrative, not dead code.
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return not _has_ellipsis_arg(call=node.value)
    return isinstance(node, _CODE_NODES)


# Block-header keywords whose comments don't parse standalone (they require a
# body). We try wrapping with ``pass`` to make them syntactically complete.
_BLOCK_HEADER_KEYWORDS = (
    "if ", "elif ", "else:", "for ", "while ", "try:", "except", "finally:",
    "with ", "match ", "case ", "def ", "async def ", "class ",
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


def _comment_parses_as_code(text: str) -> bool:
    """Return True if the comment text resolves to a code statement."""
    for candidate in _list_parsing_candidates(text):
        try:
            tree = ast.parse(candidate)
        except (SyntaxError, ValueError, RecursionError):
            # A deeply nested but parseable expression in a single comment can
            # overflow the parser. Treat it as prose, not commented-out code.
            continue
        for node in tree.body:
            if _is_code_statement(node):
                return True
            # Unwrap the synthetic ``def _():`` used for scope-bound text.
            if isinstance(node, ast.FunctionDef) and node.name == "_":
                if any(_is_code_statement(child) for child in node.body):
                    return True
    return False


def _looks_like_code(*, text: str) -> bool:
    """True if a comment body parses as a code statement rather than prose."""
    if not text or text.startswith(_PRAGMA_PREFIXES) or text.endswith((".", "?", "!")):
        return False
    return _comment_parses_as_code(text)


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


def _build_violation(
    *,
    relative_file: str,
    line: int,
    code: str,
    message: str,
    fix: str,
    column: int | None = None,
) -> Violation:
    return Violation(
        file=relative_file, line=line, rule=code, message=message, fix=fix, column=column
    )


# How far a comment block may run before CMT-002 calls it verbose is not a
# constant. A flat cap makes the rule fight COMPLEXITY-001: that rule warns at
# complexity 10 precisely because such code is hard, and then a six-line cap
# forbids explaining why it is hard. The allowance therefore grows with the
# complexity of the code the block introduces, so difficult code can carry the
# explanation it needs while a trivial helper still cannot ramble.
_BLOCK_LINES_PER_BRANCH = 2


@dataclass(frozen=True)
class _Span:
    """A function's line range and cyclomatic complexity."""

    start: int
    end: int
    complexity: int


def _collect_function_spans(*, module: Module) -> list[_Span]:
    """Line range and complexity of every function in the module."""
    spans: list[_Span] = []
    for node in module.index.functions:
        end = getattr(node, "end_lineno", node.lineno)
        spans.append(_Span(
            start=node.lineno, end=end, complexity=_measure_cyclomatic_complexity(func_node=node)
        ))
    return spans


def _measure_complexity_near(*, spans: list[_Span], start: int, end: int) -> int:
    """Complexity of the function a comment block explains.

    A block inside a function is explaining that function. A block sitting
    directly above one is its preamble, so it earns the same allowance. Anything
    else, a module-level banner or a note between definitions, gets the base.
    """
    enclosing = [span.complexity for span in spans if span.start <= start <= span.end]
    if enclosing:
        return max(enclosing)
    following = [span.complexity for span in spans if 0 <= span.start - end <= 2]
    return max(following) if following else 1


@dataclass
class CommentsCheck:
    """Concise, clean comments: commented-out code, verbosity, style, restating."""

    name: str = "comments"
    description: str = "Concise, clean comments (commented-out code, verbosity, style)"
    flag_commented_code: bool = True
    flag_verbose: bool = True
    flag_em_dash: bool = False
    flag_emoji: bool = False
    max_block_lines: int = 6
    max_comment_chars: int = 120
    block_lines_per_branch: int = _BLOCK_LINES_PER_BRANCH
    rules: list[str] = field(
        default_factory=lambda: [
            "CMT-001: No commented-out code",
            "CMT-002: No verbose comments (block or line too long)",
            "PROSE-001: No em dashes in comments or docstrings (opt-in)",
            "PROSE-003: No emoji in comments or docstrings (opt-in)",
        ]
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {
            "commented_code",
            "verbose",
            "em_dash",
            "emoji",
            "max_block_lines",
            "max_comment_chars",
            "block_lines_per_branch",
        }
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.comments]`` configuration."""
        self.flag_commented_code = is_flag_set(
            settings=settings, key="commented_code", default=self.flag_commented_code
        )
        self.flag_verbose = is_flag_set(settings=settings, key="verbose", default=self.flag_verbose)
        self.flag_em_dash = is_flag_set(settings=settings, key="em_dash", default=self.flag_em_dash)
        self.flag_emoji = is_flag_set(settings=settings, key="emoji", default=self.flag_emoji)
        self.max_block_lines = read_int(
            settings=settings, key="max_block_lines", default=self.max_block_lines
        )
        self.max_comment_chars = read_int(
            settings=settings, key="max_comment_chars", default=self.max_comment_chars
        )
        self.block_lines_per_branch = read_int(
            settings=settings, key="block_lines_per_branch", default=self.block_lines_per_branch
        )

    def _find_style_violations(
        self, *, text: str, line: int, relative_file: str, column: int | None = None
    ) -> list[Violation]:
        found: list[Violation] = []
        if self.flag_em_dash and _EM_DASH in text:
            found.append(
                _build_violation(
                    relative_file=relative_file,
                    line=line,
                    code="PROSE-001",
                    message="Em dash in comment/docstring",
                    fix="Rewrite with a comma, parentheses, or a full stop",
                    column=column,
                )
            )
        if self.flag_emoji and _EMOJI.search(text):
            found.append(
                _build_violation(
                    relative_file=relative_file,
                    line=line,
                    code="PROSE-003",
                    message="Emoji in comment/docstring",
                    fix="Remove the emoji",
                    column=column,
                )
            )
        return found

    def _find_verbose_violations(self, *, comments: list[_Comment], module: Module) -> list[Violation]:
        found: list[Violation] = []
        for comment in comments:
            if len(comment.text) > self.max_comment_chars:
                found.append(
                    _build_violation(
                        relative_file=module.relative,
                        line=comment.line,
                        code="CMT-002",
                        message=f"Comment line is {len(comment.text)} chars (limit {self.max_comment_chars})",
                        fix="Tighten it, or move the detail into a docstring",
                        column=comment.column,
                    )
                )
        found.extend(self._block_violations(comments=comments, module=module))
        return found

    def _block_violations(self, *, comments: list[_Comment], module: Module) -> list[Violation]:
        found: list[Violation] = []
        standalone = [c for c in comments if c.standalone]
        # Function complexities are only needed once a block is longer than the
        # base allowance, which most blocks never are, so they are computed on
        # first need rather than for every file.
        spans: list[_Span] | None = None
        index = 0
        while index < len(standalone):
            end = index
            while end + 1 < len(standalone) and standalone[end + 1].line == standalone[end].line + 1:
                end += 1
            length = end - index + 1
            if length <= self.max_block_lines:
                index = end + 1
                continue
            if spans is None:
                spans = _collect_function_spans(module=module)
            complexity = _measure_complexity_near(
                spans=spans, start=standalone[index].line, end=standalone[end].line
            )
            allowance = self.max_block_lines + (complexity - 1) * self.block_lines_per_branch
            if length > allowance:
                found.append(
                    _build_violation(
                        relative_file=module.relative,
                        line=standalone[index].line,
                        code="CMT-002",
                        message=(
                            f"Comment block is {length} lines (limit {allowance} "
                            f"at complexity {complexity})"
                        ),
                        fix="Tighten it, or move the detail into a docstring",
                        column=standalone[index].column,
                    )
                )
            index = end + 1
        return found

    def _scan_file(self, *, module: Module, comments: list[_Comment]) -> list[Violation]:
        found: list[Violation] = []
        relative_file = module.relative
        if self.flag_commented_code:
            metadata_lines = _find_pep723_metadata_lines(module.lines)
            found.extend(
                _build_violation(
                    relative_file=relative_file,
                    line=c.line,
                    code="CMT-001",
                    message=f"Commented-out code: {c.text[:60]}",
                    fix="Delete it; version control remembers",
                    column=c.column,
                )
                for c in comments
                if c.line not in metadata_lines and _looks_like_code(text=c.text)
            )
        if self.flag_verbose:
            found.extend(self._find_verbose_violations(comments=comments, module=module))
        if self.flag_em_dash or self.flag_emoji:
            for comment in comments:
                found.extend(self._find_style_violations(
                    text=comment.text,
                    line=comment.line,
                    relative_file=relative_file,
                    column=comment.column,
                ))
            for line, text in _collect_docstring_lines(module=module):
                found.extend(self._find_style_violations(text=text, line=line, relative_file=relative_file))
        return found

    def run(self, *, src_root: str) -> CheckResult:
        violations: list[Violation] = []
        for module in iter_parsed_modules(Path(src_root)):
            violations.extend(
                self._scan_file(
                    module=module,
                    comments=_collect_comments(source=module.source, source_lines=module.lines),
                )
            )

        return CheckResult.from_findings(check=self.name, violations=violations)


register(CommentsCheck())
