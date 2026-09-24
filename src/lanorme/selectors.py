"""Rule-code selectors: which checks a ``--check`` names, and which codes exist.

A selector is a check name, a rule code (``DRY-001``) or a category (``SIZE``),
case-insensitive. This module resolves codes and categories to the checks that
own them and rejects a selector that names nothing, so a typo in ``--select``
or ``[tool.lanorme] ignore`` is a usage error rather than a silently clean run.
"""

from __future__ import annotations

from lanorme import Check, get_all_checks, rule_code
from lanorme.errors import UsageError
from lanorme.filtering import _category


def checks_for_selector(*, selector: str) -> list[Check]:
    """Return the checks that own a rule whose code or category matches *selector*."""
    wanted = selector.upper()
    matched: list[Check] = []
    for check in get_all_checks().values():
        for rule in check.rules:
            code = rule_code(rule)
            if code == wanted or _category(code) == wanted:
                matched.append(check)
                break
    return sorted(matched, key=lambda c: c.name)


def _known_selectors() -> tuple[set[str], set[str]]:
    """The rule codes and categories the registered checks declare.

    A check whose codes are user-defined declares a ``CAT-NNN`` placeholder
    (``domain_terms``); every code in such a category is accepted.
    """
    codes = {rule_code(rule).upper() for check in get_all_checks().values() for rule in check.rules}
    categories = {code.partition("-")[0] for code in codes} | {"RUN"}
    return codes, categories


def _selector_is_known(*, selector: str, codes: set[str], categories: set[str]) -> bool:
    """True for ``ALL``, a declared code or category, a ``-000`` notice, or a placeholder's code."""
    wanted = selector.strip().upper()
    if wanted == "ALL" or wanted in codes or wanted in categories:
        return True
    category, _dash, number = wanted.partition("-")
    if category not in categories or not number:
        return False
    return number == "000" or f"{category}-NNN" in codes


def reject_unknown_selectors(*, selectors: list[str], origin: str) -> None:
    """Refuse a selector that names no known rule.

    A typo in ``--select``, ``--ignore``, ``--promote`` or their config
    counterparts used to be accepted silently, so a mistyped code produced a
    clean run rather than the narrowed one the user asked for.
    """
    codes, categories = _known_selectors()
    unknown = [
        s for s in selectors
        if s.strip() and not _selector_is_known(selector=s, codes=codes, categories=categories)
    ]
    if not unknown:
        return
    listed = ", ".join(repr(s.strip()) for s in unknown)
    raise UsageError(
        f"{origin} names no known rule code or category: {listed}.\n"
        f"  Run 'lanorme rules' to list every code and category."
    )
