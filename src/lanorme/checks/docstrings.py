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
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.checks.restating import _is_allowlisted, _split_identifier, _stem
from lanorme.discovery import iter_py_files

# Definitions shorter than this need no docstring: a three-line helper whose
# name says it all is not improved by a sentence repeating the name.
DEFAULT_MIN_LINES = 5

# Shortest signature stem allowed to swallow a longer docstring word as an
# abbreviation of it. Below this, prefix matching is noise.
MIN_ABBREVIATION = 3

# Files where a missing docstring is not a defect: package markers, fixtures
# and generated code. Mirrors the file_limits skip list.
_SKIP_FILES = frozenset({"__init__.py", "conftest.py", "setup.py"})
_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "alembic", "migrations"}
)

# Words carrying no information about what a definition does, beyond the
# grammar needed to make a sentence of the name.
_FILLER = frozenset({
    "return", "returns", "get", "gets", "set", "sets", "the", "a", "an", "to",
    "of", "and", "or", "for", "in", "on", "is", "be", "this", "that", "it",
    "with", "by", "as", "at", "from", "into", "are", "was", "were", "its",
    "given", "value", "values", "object", "objects", "function", "method",
    "class", "helper", "wrapper", "handle", "handles", "do", "does", "perform",
    "performs", "simple", "new", "one",
})

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_public(*, name: str) -> bool:
    """True if *name* is part of a module's public surface."""
    return not name.startswith("_")


def _effective_length(*, node: ast.AST) -> int:
    """Line span of a definition, docstring and decorators excluded."""
    end = getattr(node, "end_lineno", None)
    start = getattr(node, "lineno", None)
    if end is None or start is None:
        return 0
    return end - start + 1


def _signature_stems(*, node: ast.AST) -> set[str]:
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
    return {_stem(word=token) for token in raw if token}


def _docstring_stems(*, doc: str) -> set[str]:
    """Stemmed content words of a docstring, filler and punctuation removed."""
    words = _split_identifier(name=doc.replace(".", " ").replace(",", " "))
    return {_stem(word=word) for word in words if word not in _FILLER}


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
    content = _docstring_stems(doc=text)
    if not content:
        return True
    if _is_allowlisted(text=text, low=text.lower()):
        return False
    signature = _signature_stems(node=node) | {_stem(word=w) for w in _split_identifier(name=owner)}
    return all(_covers(signature=signature, word=word) for word in content)


def _skip(*, node: ast.AST, min_lines: int, require_private: bool) -> bool:
    """True if this definition is outside the rules' scope."""
    name = getattr(node, "name", "")
    if name.startswith("__") and name.endswith("__"):
        return True
    if not require_private and not _is_public(name=name):
        return True
    return _effective_length(node=node) < min_lines


def _noun(*, node: ast.AST) -> str:
    """The word for what *node* is, for use in a message."""
    return "Class" if isinstance(node, ast.ClassDef) else "Function"


def _owners(*, tree: ast.Module) -> dict[int, str]:
    """Map each method to its enclosing class name, keyed by node id.

    A method's docstring is read next to its class, so ``Refill the bucket.``
    on ``Bucket.refill`` restates the pair and adds nothing.
    """
    owned: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, _DEF_TYPES):
                    owned[id(child)] = node.name
    return owned


def _definition_violations(*, tree: ast.Module, file: str, min_lines: int, require_private: bool) -> list[Violation]:
    """Check every in-scope definition in one module for CMT-006 and CMT-007."""
    violations: list[Violation] = []
    owned = _owners(tree=tree)
    for node in ast.walk(tree):
        if not isinstance(node, _DEF_TYPES):
            continue
        if _skip(node=node, min_lines=min_lines, require_private=require_private):
            continue
        doc = ast.get_docstring(node)
        if doc is None:
            violations.append(Violation(
                file=file,
                line=node.lineno,
                rule="CMT-006: Public definitions past the size floor need a docstring",
                message=f"{_noun(node=node)} '{node.name}' has no docstring",
                fix="Say what it is for, or what a caller needs to know that the signature does not show",
            ))
        elif _is_vacuous(doc=doc, node=node, owner=owned.get(id(node), "")):
            violations.append(Violation(
                file=file,
                line=node.lineno,
                rule="CMT-007: A docstring must say more than the signature",
                message=f"Docstring of '{node.name}' only restates its name and parameters",
                fix="Add what the signature cannot show: the why, a caveat, a unit, or a reference",
            ))
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
        ]
    )

    def configure(self, *, settings: dict[str, bool | int]) -> None:
        """Apply ``[tool.lanorme.docstrings]`` configuration."""
        if "enabled" in settings:
            self.enabled = bool(settings["enabled"])
        if "min_lines" in settings:
            self.min_lines = int(settings["min_lines"])
        if "require_private" in settings:
            self.require_private = bool(settings["require_private"])

    def run(self, *, src_root: str) -> CheckResult:
        """Walk every Python file and collect CMT-006 / CMT-007 violations."""
        if not self.enabled:
            return CheckResult(check=self.name, status=Status.PASS, violations=[])
        violations: list[Violation] = []
        root = Path(src_root)
        for path in iter_py_files(root):
            if any(part in _SKIP_DIRS for part in path.parts) or path.name in _SKIP_FILES:
                continue
            if path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            violations.extend(_definition_violations(
                tree=tree,
                file=str(path.relative_to(root)),
                min_lines=self.min_lines,
                require_private=self.require_private,
            ))
        status = Status.FAIL if violations else Status.PASS
        return CheckResult(check=self.name, status=status, violations=violations)


register(DocstringsCheck())
