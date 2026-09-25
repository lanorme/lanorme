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

from dataclasses import dataclass, field
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set, read_int
from lanorme.checks.file_limits import _measure_cyclomatic_complexity
from lanorme.comment_code import (
    Comment,
    _find_illustrative_lines,
    _find_pep723_metadata_lines,
    _is_licence_block,
    _looks_like_code,
    _measure_prose_length,
)
from lanorme.lexical_scopes import ModuleBindings
from lanorme.markdown import EMOJI_RE
from lanorme.scan import Scan
from lanorme.sources import Module, iter_parsed_modules

_EM_DASH = "—"


def _collect_docstring_lines(*, module: Module) -> list[tuple[int, str]]:
    """Return (line, text) for each line of every module/class/function docstring."""
    return [
        (docstring.line + offset, text)
        for docstring in module.docstrings
        for offset, text in enumerate(docstring.text.splitlines())
    ]


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
        file=relative_file,
        line=line,
        rule=code,
        message=message,
        fix=fix,
        column=column,
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
        # A preamble sits above the decorators, so the span starts at the first
        # of them, not at the ``def`` line the decorators push down.
        start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        spans.append(
            _Span(
                start=start,
                end=end,
                complexity=_measure_cyclomatic_complexity(func_node=node),
            ),
        )
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
        ],
    )
    # The PROSE rules are off until their flag is set; the reference says so.
    opt_in_rules: ClassVar[frozenset[str]] = frozenset({"PROSE-001", "PROSE-003"})
    opt_in_settings: ClassVar[dict[str, str]] = {"PROSE-001": "em_dash", "PROSE-003": "emoji"}
    settings_keys: ClassVar[frozenset[str]] = frozenset(
        {
            "commented_code",
            "verbose",
            "em_dash",
            "emoji",
            "max_block_lines",
            "max_comment_chars",
            "block_lines_per_branch",
        },
    )

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.comments]`` configuration."""
        self.flag_commented_code = is_flag_set(
            settings=settings,
            key="commented_code",
            default=self.flag_commented_code,
        )
        self.flag_verbose = is_flag_set(settings=settings, key="verbose", default=self.flag_verbose)
        self.flag_em_dash = is_flag_set(settings=settings, key="em_dash", default=self.flag_em_dash)
        self.flag_emoji = is_flag_set(settings=settings, key="emoji", default=self.flag_emoji)
        self.max_block_lines = read_int(
            settings=settings,
            key="max_block_lines",
            default=self.max_block_lines,
        )
        self.max_comment_chars = read_int(
            settings=settings,
            key="max_comment_chars",
            default=self.max_comment_chars,
        )
        self.block_lines_per_branch = read_int(
            settings=settings,
            key="block_lines_per_branch",
            default=self.block_lines_per_branch,
        )

    def _find_style_violations(
        self,
        *,
        text: str,
        line: int,
        relative_file: str,
        column: int | None = None,
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
                ),
            )
        if self.flag_emoji and EMOJI_RE.search(text):
            found.append(
                _build_violation(
                    relative_file=relative_file,
                    line=line,
                    code="PROSE-003",
                    message="Emoji in comment/docstring",
                    fix="Remove the emoji",
                    column=column,
                ),
            )
        return found

    def _find_verbose_violations(
        self,
        *,
        comments: list[Comment],
        module: Module,
        metadata_lines: frozenset[int],
    ) -> list[Violation]:
        found: list[Violation] = []
        for comment in comments:
            length = _measure_prose_length(comment.text)
            if length > self.max_comment_chars:
                found.append(
                    _build_violation(
                        relative_file=module.relative,
                        line=comment.line,
                        code="CMT-002",
                        message=f"Comment line is {length} chars (limit {self.max_comment_chars})",
                        fix="Tighten it, or move the detail into a docstring",
                        column=comment.column,
                    ),
                )
        found.extend(
            self._block_violations(
                comments=comments,
                module=module,
                metadata_lines=metadata_lines,
            ),
        )
        return found

    def _block_violations(
        self,
        *,
        comments: list[Comment],
        module: Module,
        metadata_lines: frozenset[int],
    ) -> list[Violation]:
        found: list[Violation] = []
        standalone = [c for c in comments if c.standalone and c.line not in metadata_lines]
        # Function complexities are only needed once a block is longer than the
        # base allowance, which most blocks never are, so they are computed on
        # first need rather than for every file.
        spans: list[_Span] | None = None
        index = 0
        while index < len(standalone):
            end = index
            while (
                end + 1 < len(standalone) and standalone[end + 1].line == standalone[end].line + 1
            ):
                end += 1
            length = end - index + 1
            if length <= self.max_block_lines or _is_licence_block(standalone[index : end + 1]):
                index = end + 1
                continue
            if spans is None:
                spans = _collect_function_spans(module=module)
            complexity = _measure_complexity_near(
                spans=spans,
                start=standalone[index].line,
                end=standalone[end].line,
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
                    ),
                )
            index = end + 1
        return found

    def _scan_file(self, *, module: Module, comments: list[Comment]) -> list[Violation]:
        found: list[Violation] = []
        relative_file = module.relative
        metadata_lines = _find_pep723_metadata_lines(module.lines)
        if self.flag_commented_code:
            exempt = metadata_lines | _find_illustrative_lines(comments)
            names = ModuleBindings(module)
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
                if c.line not in exempt and _looks_like_code(text=c.text, names=names)
            )
        if self.flag_verbose:
            found.extend(
                self._find_verbose_violations(
                    comments=comments,
                    module=module,
                    metadata_lines=metadata_lines,
                ),
            )
        if self.flag_em_dash or self.flag_emoji:
            for comment in comments:
                found.extend(
                    self._find_style_violations(
                        text=comment.text,
                        line=comment.line,
                        relative_file=relative_file,
                        column=comment.column,
                    ),
                )
            for line, text in _collect_docstring_lines(module=module):
                found.extend(
                    self._find_style_violations(text=text, line=line, relative_file=relative_file),
                )
        return found

    def check(self, scan: Scan) -> CheckResult:
        violations: list[Violation] = []
        for module in iter_parsed_modules(scan.root):
            violations.extend(
                self._scan_file(
                    module=module,
                    comments=list(module.comments),
                ),
            )

        return CheckResult.from_findings(check=self.name, violations=violations)


register(CommentsCheck())
