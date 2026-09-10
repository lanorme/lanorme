"""NAMING-009..011: the Clean Code reading of the naming canon, opt-in.

Chapter 2 of Clean Code (Martin, "Meaningful Names") goes past the canon in
two places. It names the noise words to keep off a class (Manager, Processor,
Data, Info: a job title or a shrug where a thing should be), and it wants every
method to be a verb or verb phrase, with accessors and predicates carrying
``get``, ``set`` and ``is``. The junk-drawer module (``utils``, ``helpers``,
``common``) is the module-level noise word, and Go's package-naming advice says
so in as many words: avoid ``util``, ``common`` and ``misc``.

The last rule is a house choice, not a correction. Naming a pure function for
the value it returns (``basename``, ``len``, a property) is the other canonical
school, and it is the one Python's own library follows. A project that opts
in here is choosing the Java-school reading for itself; the default-on
``naming_canon`` check already covers what both schools agree on.

Default-off. Opt in via::

    [tool.lanorme.naming_clean_code]
    enabled = true
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.checks.naming_canon import verb_fix
from lanorme.checks.naming_shapes import (
    FUNCTION_TYPES,
    Definition,
    decorator_leaves,
    is_command,
    is_exempt,
    is_framework_named,
    is_raiser,
    iter_definitions,
    iter_modules,
    name_setting,
)
from lanorme.checks.naming_words import (
    JUNK_MODULES,
    NOISE_WORDS,
    is_pascal_case,
    is_predicate,
    leading_verb_index,
    split_name,
)

RULE_009 = "NAMING-009: A class name carries no noise word (Manager, Processor, Data, Info, Helper, Util)"
RULE_010 = "NAMING-010: A module is not a junk drawer (utils, helpers, common, misc)"
RULE_011 = "NAMING-011: Every function starts with a verb"


@dataclass(frozen=True)
class _Settings:
    """Resolved vocabulary for one run."""

    verbs: frozenset[str]
    exempt: frozenset[str]


def _noise_findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """NAMING-009: a class whose last word is a job title or a shrug."""
    name = definition.name
    tokens = split_name(name=name)
    if is_exempt(name=name, exempt=settings.exempt) or not is_pascal_case(name=name):
        return []
    if len(tokens) < 2 or tokens[-1] not in NOISE_WORDS:
        return []
    if tokens[-2:] == ["meta", "data"] or name.endswith("ContextManager"):
        return []
    return [Violation(
        file=file,
        line=definition.node.lineno,
        rule=RULE_009,
        message=f"Class '{name}' ends in '{tokens[-1]}', a noise word that names a job title, not a thing",
        fix="Say what it is (a Registry, a Pool, a Cache, a Scheduler) or what it holds (an Order, a Profile)",
    )]


def _junk_module_findings(*, file: str, settings: _Settings) -> list[Violation]:
    """NAMING-010: a module or package named for having no name."""
    path = Path(file)
    stem = path.parent.name if path.name == "__init__.py" else path.stem
    if stem not in JUNK_MODULES or is_exempt(name=stem, exempt=settings.exempt):
        return []
    kind = "Package" if path.name == "__init__.py" else "Module"
    return [Violation(
        file=file,
        line=0,
        rule=RULE_010,
        message=f"{kind} '{stem}' is a junk drawer: the name promises nothing about what is inside",
        fix="Split it by responsibility and name each module for what it holds (paths.py, dates.py, ...)",
    )]


def _verb_findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """NAMING-011: a query that does not lead with a verb.

    Commands are NAMING-007's and a raiser exists to raise, so this rule takes
    the rest: functions that answer with a value, and stubs. Constructors under
    ``@classmethod`` are named for what they build, and a predicate reads as
    an assertion.
    """
    name = definition.name
    if is_exempt(name=name, exempt=settings.exempt) or is_framework_named(definition=definition):
        return []
    node = definition.node
    if "classmethod" in decorator_leaves(node=node) or is_command(node=node) or is_raiser(node=node):
        return []
    tokens = split_name(name=name)
    if not tokens or leading_verb_index(tokens=tokens, extra=settings.verbs) >= 0 or is_predicate(tokens=tokens):
        return []
    return [Violation(
        file=file,
        line=node.lineno,
        rule=RULE_011,
        message=f"Function '{name}' does not start with a verb",
        fix=verb_fix(
            name=name,
            tokens=tokens,
            verbs=settings.verbs,
            otherwise=(
                "Lead with what it does to get the value (find_, build_, compute_, load_), "
                "or is_/has_ for a predicate"
            ),
        ),
    )]


def _findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """Every NAMING-009 and NAMING-011 finding on one definition."""
    if isinstance(definition.node, FUNCTION_TYPES):
        return _verb_findings(definition=definition, file=file, settings=settings)
    return _noise_findings(definition=definition, file=file, settings=settings)


@dataclass
class NamingCleanCodeCheck:
    """NAMING-009..011: Clean Code's naming chapter, enforced (opt-in)."""

    name: str = "naming_clean_code"
    description: str = (
        "Clean Code naming: no noise words on classes, no junk-drawer modules, "
        "every function a verb (NAMING-009..011)"
    )
    enabled: bool = False
    verbs: frozenset[str] = frozenset()
    exempt: frozenset[str] = frozenset()
    rules: list[str] = field(default_factory=lambda: [RULE_009, RULE_010, RULE_011])

    def configure(self, *, settings: dict[str, bool | list[str]]) -> None:
        """Apply ``[tool.lanorme.naming_clean_code]`` configuration."""
        if "enabled" in settings:
            self.enabled = bool(settings["enabled"])
        verbs = name_setting(settings=settings, key="verbs")
        if verbs is not None:
            self.verbs = frozenset(word.lower() for word in verbs)
        exempt = name_setting(settings=settings, key="exempt")
        if exempt is not None:
            self.exempt = frozenset(exempt)

    def run(self, *, src_root: str) -> CheckResult:
        """Walk every module and collect NAMING-009..011 warnings, in source order."""
        if not self.enabled:
            return CheckResult(check=self.name, status=Status.PASS)
        settings = _Settings(verbs=self.verbs, exempt=self.exempt)
        warnings: list[Violation] = []
        for relative, tree in iter_modules(root=Path(src_root)):
            warnings.extend(_junk_module_findings(file=relative, settings=settings))
            for definition in iter_definitions(tree=tree):
                warnings.extend(_findings(definition=definition, file=relative, settings=settings))
        warnings.sort(key=lambda warning: (warning.file, warning.line))
        status = Status.WARN if warnings else Status.PASS
        return CheckResult(check=self.name, status=status, warnings=warnings)


register(NamingCleanCodeCheck())
