"""NAMING-006..008: the part of the naming canon every school agrees on.

A class is a thing, so its name is a noun phrase. Clean Code (Martin, ch. 2)
says a class name should not be a verb; the Java Code Conventions, the .NET
Framework Design Guidelines and the Kotlin coding conventions say the same in
their own words. The one verb-first class the canon admits is the message
object of CQRS, and there the convention is a noun suffix (``CreateUserCommand``).

A function that does something is named as doing it, verb first. Kernighan and
Pike (The Practice of Programming, 1.1) call these active names; Code Complete
(McConnell, 7.3) says a procedure gets a strong verb followed by an object.

And the verb must say what happens. Code Complete lists ``HandleCalculation``,
``PerformServices``, ``ProcessInput`` and ``DealWithOutput`` as the shapes to
avoid; ``do_`` is the Python spelling of the same evasion.

What the rules leave alone is the other half of Code Complete's advice: a
function that returns a value may be named for the value (``basename``,
``len``, a property). That is the command-query split of Meyer's Eiffel, the
Ada style guide, Swift, Kotlin, Go and Rust, and it is how Python names things.
The opt-in ``naming_clean_code`` check enforces the stricter Java-school
reading, where queries lead with a verb too.

Default-on, warnings. Calibration and measured precision: ``docs/RULES.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckResult, Status, Violation, register
from lanorme.checks.naming_shapes import (
    FUNCTION_TYPES,
    Definition,
    is_command,
    is_exempt,
    is_framework_named,
    iter_definitions,
    iter_modules,
    name_setting,
)
from lanorme.checks.naming_words import (
    COMMAND_SUFFIXES,
    VERB_ONLY,
    WEAK_VERBS,
    is_noun_phrase,
    is_pascal_case,
    leading_verb_index,
    modifier_count,
    postposed_verb_index,
    split_name,
    verb_first,
)

RULE_006 = "NAMING-006: A class is named as a thing, not as an action"
RULE_007 = "NAMING-007: A function that does something is named verb-first"
RULE_008 = "NAMING-008: A function does not open with a weak verb (handle, process, perform, do, manage)"


@dataclass(frozen=True)
class _Settings:
    """Resolved vocabulary for one run."""

    verbs: frozenset[str]
    command_suffixes: tuple[str, ...]
    weak_verbs: frozenset[str]
    exempt: frozenset[str]


# How a verb becomes its doer, by ending: validate gives validator, execute
# gives executor, collect gives collector, emit gives emitter, parse gives parser.
_AGENT_ENDINGS: tuple[tuple[str, str], ...] = (
    ("ate", "ator"), ("ute", "utor"), ("ct", "ctor"), ("mit", "mitter"),
    ("fer", "ferrer"), ("trol", "troller"), ("e", "er"),
)


def agent_noun(*, verb: str) -> str:
    """The doer of *verb*: validate gives validator, notify gives notifier, get gives getter."""
    for ending, agent in _AGENT_ENDINGS:
        if verb.endswith(ending):
            return f"{verb[: -len(ending)]}{agent}"
    if verb.endswith("y") and verb[-2:-1] not in "aeiou":
        return f"{verb[:-1]}ier"
    if len(verb) == 3 and verb[-1] not in "aeiouwxy":
        return f"{verb}{verb[-1]}er"
    return f"{verb}er"


def _thing_name(*, name: str, tokens: list[str]) -> str:
    """A noun-phrase rename for a verb-first class: ``FetchUsers`` gives ``UsersFetcher``.

    Leading underscores stay in front and digits move to the end, so the
    suggestion is always an identifier.
    """
    prefix = name[: len(name) - len(name.lstrip("_"))]
    words = "".join(token.capitalize() for token in tokens[1:] if not token.isdigit())
    digits = "".join(token for token in tokens[1:] if token.isdigit())
    return f"{prefix}{words}{agent_noun(verb=tokens[0]).capitalize()}{digits}"


def _class_findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """NAMING-006: a class named as an action."""
    name = definition.name
    if not is_pascal_case(name=name) or name.endswith(settings.command_suffixes):
        return []
    tokens = split_name(name=name)
    if is_exempt(name=name, exempt=settings.exempt) or len(tokens) < 2:
        return []
    if tokens[0] not in VERB_ONLY or is_noun_phrase(tokens=tokens):
        return []
    return [Violation(
        file=file,
        line=definition.node.lineno,
        rule=RULE_006,
        message=f"Class '{name}' is named as an action: '{tokens[0]}' is a verb, but a class is a thing",
        fix=(
            f"Name it for what it is (for example '{_thing_name(name=name, tokens=tokens)}'), "
            "or mark a message object with a suffix such as 'Command'"
        ),
    )]


def verb_fix(*, name: str, tokens: list[str], verbs: frozenset[str], otherwise: str) -> str:
    """The rename to suggest: the trailing verb moved first when there is one, else *otherwise*."""
    later = postposed_verb_index(tokens=tokens, extra=verbs)
    if later > 0:
        return f"Put the verb first: '{verb_first(name=name, tokens=tokens, index=later)}'"
    return otherwise


def _command_findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """NAMING-007: a function that acts but is not named as acting."""
    name = definition.name
    if is_exempt(name=name, exempt=settings.exempt) or is_framework_named(definition=definition):
        return []
    tokens = split_name(name=name)
    if not tokens or leading_verb_index(tokens=tokens, extra=settings.verbs) >= 0:
        return []
    if not is_command(node=definition.node):
        return []
    judged = tokens[modifier_count(tokens=tokens)]
    return [Violation(
        file=file,
        line=definition.node.lineno,
        rule=RULE_007,
        message=(
            f"Function '{name}' does something and returns nothing, "
            f"but '{judged}' does not read as a verb"
        ),
        fix=verb_fix(
            name=name,
            tokens=tokens,
            verbs=settings.verbs,
            otherwise="Start with the verb for what it does (write_, register_, apply_, record_, ...)",
        ),
    )]


def _weak_verb_findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """NAMING-008: a function whose verb says nothing about what happens."""
    name = definition.name
    if is_exempt(name=name, exempt=settings.exempt) or definition.may_override:
        return []
    if is_framework_named(definition=definition):
        return []
    tokens = split_name(name=name)
    if len(tokens) < 2 or tokens[0] not in settings.weak_verbs:
        return []
    rest = "_".join(tokens[2:] if tokens[:2] == ["deal", "with"] else tokens[1:])
    if not rest:
        return []
    return [Violation(
        file=file,
        line=definition.node.lineno,
        rule=RULE_008,
        message=(
            f"Function '{name}' opens with '{tokens[0]}', which says something happens "
            f"to '{rest}' without saying what"
        ),
        fix=f"Name the action: parse_{rest}, store_{rest}, validate_{rest}, ...",
    )]


def _findings(*, definition: Definition, file: str, settings: _Settings) -> list[Violation]:
    """Every NAMING-006..008 finding on one definition."""
    if not isinstance(definition.node, FUNCTION_TYPES):
        return _class_findings(definition=definition, file=file, settings=settings)
    return [
        *_command_findings(definition=definition, file=file, settings=settings),
        *_weak_verb_findings(definition=definition, file=file, settings=settings),
    ]


@dataclass
class NamingCanonCheck:
    """NAMING-006..008: classes are things, acting functions are verbs, and the verb says what happens."""

    name: str = "naming_canon"
    description: str = (
        "Classes named as things, acting functions named verb-first, no weak verbs (NAMING-006..008)"
    )
    verbs: frozenset[str] = frozenset()
    command_suffixes: tuple[str, ...] = COMMAND_SUFFIXES
    weak_verbs: frozenset[str] = WEAK_VERBS
    exempt: frozenset[str] = frozenset()
    rules: list[str] = field(default_factory=lambda: [RULE_006, RULE_007, RULE_008])

    def configure(self, *, settings: dict[str, bool | list[str]]) -> None:
        """Apply ``[tool.lanorme.naming_canon]``.

        ``verbs`` and ``command_suffixes`` extend the defaults; ``weak_verbs``
        and ``exempt`` replace them.
        """
        verbs = name_setting(settings=settings, key="verbs")
        if verbs is not None:
            self.verbs = frozenset(word.lower() for word in verbs)
        suffixes = name_setting(settings=settings, key="command_suffixes")
        if suffixes is not None:
            self.command_suffixes = (*COMMAND_SUFFIXES, *suffixes)
        weak = name_setting(settings=settings, key="weak_verbs")
        if weak is not None:
            self.weak_verbs = frozenset(word.lower() for word in weak)
        exempt = name_setting(settings=settings, key="exempt")
        if exempt is not None:
            self.exempt = frozenset(exempt)

    def run(self, *, src_root: str) -> CheckResult:
        """Walk every module and collect NAMING-006..008 warnings, in source order."""
        settings = _Settings(
            verbs=self.verbs | self.weak_verbs,
            command_suffixes=self.command_suffixes,
            weak_verbs=self.weak_verbs,
            exempt=self.exempt,
        )
        warnings: list[Violation] = []
        for relative, tree in iter_modules(root=Path(src_root)):
            for definition in iter_definitions(tree=tree):
                warnings.extend(_findings(definition=definition, file=relative, settings=settings))
        warnings.sort(key=lambda warning: (warning.file, warning.line))
        status = Status.WARN if warnings else Status.PASS
        return CheckResult(check=self.name, status=status, warnings=warnings)


register(NamingCanonCheck())
