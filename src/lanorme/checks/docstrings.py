"""CMT-006 / CMT-007: docstrings that exist, and that say something.

Every other comment rule in LaNorme subtracts. CMT-001 deletes commented-out
code, CMT-002 caps comment length, CMT-005 deletes comments that restate the
next line, PROSE-001/003 strip em dashes and emoji. Nothing requires a
docstring to exist, so the cheapest way to satisfy the comment family is to
write nothing, and the measured result is a corpus of generated code at 31%
median docstring coverage that reports clean (``evals/readability``).

These two rules point the other way:

    CMT-006  a public definition past a size floor carries a docstring
    CMT-007  that docstring is not merely the signature spelled out

CMT-007 is the load-bearing half. A bare existence requirement is satisfied by
``\"\"\"Go.\"\"\"``, so the rule reuses the vocabulary machinery behind CMT-005:
a docstring is vacuous when every content word in it already appears in the
name and parameters it documents. Padding does not help, because padding is
restatement. Writing down something the signature does not already say is the
only way through.

Precision-first, like CMT-005. A docstring is flagged only when *all* of its
content words map onto the signature, and the CMT-005 allowlist exempts any
docstring carrying a why, a caveat, a unit or a reference.

Default-off (opinionated). Opt in via::

    [tool.lanorme.docstrings]
    enabled = true

Run:
    lanorme check . --check=docstrings
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

from lanorme import CheckResult, Violation, register
from lanorme.checkconfig import is_flag_set, read_int
from lanorme.checks.restating import _is_allowlisted, _split_identifier, _strip_suffix
from lanorme.paths import is_test_file
from lanorme.scan import Scan
from lanorme.sources import Module, iter_parsed_modules, locate

# Definitions shorter than this need no docstring: a three-line helper whose
# name says it all is not improved by a sentence repeating the name.
DEFAULT_MIN_LINES = 5

# Shortest signature stem allowed to swallow a longer docstring word as an
# abbreviation of it. Below this, prefix matching is noise.
MIN_ABBREVIATION = 3

# Files where a missing docstring is not a defect: package markers and
# generated code. Test files are exempt through ``lanorme.paths``.
_SKIP_FILES = frozenset({"__init__.py", "setup.py"})
_SKIP_DIRS = frozenset({"alembic", "migrations"})

# Words carrying no information about what a definition does, beyond the
# grammar needed to make a sentence of the name.
_FILLER = frozenset(
    {
        "return",
        "returns",
        "get",
        "gets",
        "set",
        "sets",
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "or",
        "for",
        "in",
        "on",
        "is",
        "be",
        "this",
        "that",
        "it",
        "with",
        "by",
        "as",
        "at",
        "from",
        "into",
        "are",
        "was",
        "were",
        "its",
        "given",
        "value",
        "values",
        "object",
        "objects",
        "function",
        "method",
        "class",
        "helper",
        "wrapper",
        "handle",
        "handles",
        "do",
        "does",
        "perform",
        "performs",
        "simple",
        "new",
        "one",
        # A placeholder left where a docstring should go says nothing either.
        "todo",
        "tbd",
        "fixme",
        "xxx",
        "wip",
        "stub",
        "placeholder",
        "docstring",
        "doc",
        "docs",
        "documentation",
        "description",
    },
)

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_public(*, name: str) -> bool:
    """True if *name* is part of a module's public surface."""
    return not name.startswith("_")


def _measure_effective_length(*, node: ast.AST) -> int:
    """Line span of a definition, docstring and decorators excluded."""
    end = getattr(node, "end_lineno", None)
    start = getattr(node, "lineno", None)
    if end is None or start is None:
        return 0
    return end - start + 1


def _collect_signature_stems(*, node: ast.AST) -> set[str]:
    """Stemmed vocabulary a reader already has from the name and parameters."""
    raw: list[str] = list(_split_identifier(name=getattr(node, "name", "")))
    args = getattr(node, "args", None)
    if args is not None:
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg:
            every.append(args.vararg)
        if args.kwarg:
            every.append(args.kwarg)
        for arg in every:
            raw.extend(_split_identifier(name=arg.arg))
    return {_strip_suffix(word=token) for token in raw if token}


def _collect_docstring_stems(*, doc: str) -> set[str]:
    """Stemmed content words of a docstring, filler and punctuation removed."""
    words = _split_identifier(name=doc.replace(".", " ").replace(",", " "))
    return {_strip_suffix(word=word) for word in words if word not in _FILLER}


def _covers(*, signature: set[str], word: str) -> bool:
    """True if *word* is already carried by the signature vocabulary.

    Equality catches inflection, which the stemmer handles. Prefix matching
    catches abbreviation, which it cannot: ``proc`` and ``process`` share no
    stem, but a reader learns nothing from the second having seen the first.
    The prefix floor keeps two-letter names such as ``go`` from swallowing
    unrelated words like ``govern``.
    """
    return any(
        word == stem
        or (len(stem) >= MIN_ABBREVIATION and (word.startswith(stem) or stem.startswith(word)))
        for stem in signature
    )


def _is_vacuous(*, doc: str, node: ast.AST, owner: str = "") -> bool:
    """True if *doc* tells a reader nothing the signature had not already said.

    Emptiness is settled before the allowlist. CMT-005's allowlist is tuned for
    comments and spares anything carrying a why or a caveat, but a docstring
    with no content word left after filler removal carries neither, so
    ``This is a helper function.`` must not be rescued by it.
    """
    text = doc.strip()
    if not text:
        return True
    content = _collect_docstring_stems(doc=text)
    if not content:
        return True
    if _is_allowlisted(text=text, low=text.lower()):
        return False
    signature = _collect_signature_stems(node=node) | {
        _strip_suffix(word=w) for w in _split_identifier(name=owner)
    }
    return all(_covers(signature=signature, word=word) for word in content)


@dataclass(frozen=True)
class _Definition:
    """A definition on the module's surface, with the class it belongs to.

    A method's docstring is read next to its class, so ``Refill the bucket.``
    on ``Bucket.refill`` restates the pair and adds nothing. ``hidden`` marks a
    member of a private class, which is no more public than the class.
    """

    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    owner: str
    hidden: bool


def _skip(*, definition: _Definition, min_lines: int, require_private: bool) -> bool:
    """True if this definition is outside the rules' scope."""
    name = definition.node.name
    if name.startswith("__") and name.endswith("__"):
        return True
    if not require_private and (definition.hidden or not _is_public(name=name)):
        return True
    return _measure_effective_length(node=definition.node) < min_lines


def _describe_node(*, node: ast.AST) -> str:
    """The word for what *node* is, for use in a message."""
    return "Class" if isinstance(node, ast.ClassDef) else "Function"


def _iter_nested_bodies(node: ast.stmt) -> Iterator[list[ast.stmt]]:
    """The statement lists a module-level ``if`` or ``try`` can hold a definition in."""
    if isinstance(node, ast.If):
        yield node.body
        yield node.orelse
    elif isinstance(node, ast.Try):
        yield node.body
        yield node.orelse
        yield node.finalbody
        for handler in node.handlers:
            yield handler.body


def _collect_definitions(
    body: list[ast.stmt],
    *,
    owner: str = "",
    hidden: bool = False,
) -> Iterator[_Definition]:
    """Every definition a reader documents: module level and class members.

    A function nested inside another is an implementation detail of its
    parent, not part of any public surface, so a function body is never
    descended into. A class body is, so methods and nested classes are found.
    """
    for node in body:
        if isinstance(node, _DEF_TYPES):
            yield _Definition(node=node, owner=owner, hidden=hidden)
            if isinstance(node, ast.ClassDef):
                yield from _collect_definitions(
                    node.body,
                    owner=node.name,
                    hidden=hidden or not _is_public(name=node.name),
                )
            continue
        for nested in _iter_nested_bodies(node):
            yield from _collect_definitions(nested, owner=owner, hidden=hidden)


def _find_definition_violations(
    *,
    module: Module,
    min_lines: int,
    require_private: bool,
) -> list[Violation]:
    """Check every in-scope definition in one module for CMT-006 and CMT-007."""
    violations: list[Violation] = []
    file = module.relative
    for definition in _collect_definitions(module.tree.body):
        if _skip(definition=definition, min_lines=min_lines, require_private=require_private):
            continue
        node = definition.node
        docstring = module.find_docstring(node)
        doc = docstring.clean() if docstring is not None else None
        if doc is None:
            violations.append(
                Violation(
                    file=file,
                    line=node.lineno,
                    rule="CMT-006: Public definitions past the size floor need a docstring",
                    message=f"{_describe_node(node=node)} '{node.name}' has no docstring",
                    fix="Say what it is for, or what a caller needs to know that the signature does not show",
                    **locate(node),
                ),
            )
        elif _is_vacuous(doc=doc, node=node, owner=definition.owner):
            violations.append(
                Violation(
                    file=file,
                    line=node.lineno,
                    rule="CMT-007: A docstring must say more than the signature",
                    message=f"Docstring of '{node.name}' only restates its name and parameters",
                    fix="Add what the signature cannot show: the why, a caveat, a unit, or a reference",
                    **locate(node),
                ),
            )
    return violations


@dataclass
class DocstringsCheck:
    """CMT-006 / CMT-007: docstrings exist, and carry more than the signature (opt-in)."""

    name: str = "docstrings"
    description: str = "Docstrings that exist and say something (CMT-006, CMT-007)"
    enabled: bool = False
    min_lines: int = DEFAULT_MIN_LINES
    require_private: bool = False
    rules: list[str] = field(
        default_factory=lambda: [
            "CMT-006: Public definitions past the size floor need a docstring",
            "CMT-007: A docstring must say more than the signature",
        ],
    )
    settings_keys: ClassVar[frozenset[str]] = frozenset({"enabled", "min_lines", "require_private"})

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply ``[tool.lanorme.docstrings]`` configuration."""
        self.enabled = is_flag_set(settings=settings, key="enabled", default=self.enabled)
        self.min_lines = read_int(settings=settings, key="min_lines", default=self.min_lines)
        self.require_private = is_flag_set(
            settings=settings,
            key="require_private",
            default=self.require_private,
        )

    def check(self, scan: Scan) -> CheckResult:
        """Walk every Python file and collect CMT-006 / CMT-007 violations."""
        if not self.enabled:
            return CheckResult.from_findings(check=self.name)
        violations: list[Violation] = []
        for module in iter_parsed_modules(scan.root):
            # Match skip directories inside the root only: the absolute path's
            # ancestors are the user's filesystem, not the project layout.
            if (
                any(part in _SKIP_DIRS for part in module.relative.split("/"))
                or module.path.name in _SKIP_FILES
                or is_test_file(module.relative)
            ):
                continue
            violations.extend(
                _find_definition_violations(
                    module=module,
                    min_lines=self.min_lines,
                    require_private=self.require_private,
                ),
            )
        return CheckResult.from_findings(check=self.name, violations=violations)


register(DocstringsCheck())
