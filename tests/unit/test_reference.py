"""``lanorme rule`` and ``lanorme rules``: exact resolution, honest boundaries, JSON forms."""

from __future__ import annotations

import json

import pytest

from lanorme.cli import _load_builtin_checks, main
from lanorme.errors import UsageError
from lanorme.reference import _collect_headings, _find_section_bounds, describe_rule


def _run(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def test_rule_resolves_a_code_inside_its_family_range(capsys):
    # Arrange: NAMING-004 is documented under the ``NAMING-001..004`` heading only.
    _load_builtin_checks()

    # Act.
    code = _run(["rule", "NAMING-004"])
    out = capsys.readouterr().out

    # Assert: the declaration line, the owning check, and the right family section.
    assert code == 0
    assert out.startswith("NAMING-004: Boolean functions should use")
    assert "check: naming_consistency (on by default)" in out
    assert "NAMING-001..004" in out and "Naming canon" not in out


def test_rule_prefers_the_exact_heading_over_the_family(capsys):
    # Arrange.
    _load_builtin_checks()

    # Act.
    _run(["rule", "NAMING-007"])
    out = capsys.readouterr().out

    # Assert: the ``### `NAMING-007``` section, not the whole canon chapter.
    assert "### `NAMING-007`" in out and "### `NAMING-006`" not in out


def test_section_boundaries_ignore_headings_inside_fenced_code():
    # Arrange: a TOML comment inside a fence looks like a heading to a naive scan.
    lines = [
        "## Family: `X-001..002`",
        "prose",
        "```toml",
        "# not a heading",
        "```",
        "more prose",
        "## Next: `Y-001`",
    ]

    # Act.
    bounds = _find_section_bounds(headings=_collect_headings(lines), code="X-001", total=len(lines))

    # Assert: the section runs to the real next heading.
    assert bounds == (0, 6)


def test_rule_json_carries_declaration_and_section(capsys):
    # Arrange.
    _load_builtin_checks()

    # Act.
    _run(["rule", "kwarg-001", "--json"])
    detail = json.loads(capsys.readouterr().out)

    # Assert.
    assert detail["code"] == "KWARG-001" and detail["check"] == "named_args"
    assert detail["opt_in"] is True and detail["section"].startswith("## Keyword arguments")


def test_rules_json_lists_every_check(capsys):
    # Arrange.
    _load_builtin_checks()

    # Act.
    _run(["rules", "--json"])
    listing = json.loads(capsys.readouterr().out)

    # Assert.
    names = {entry["check"] for entry in listing}
    assert {"file_limits", "meta", "named_args"} <= names
    assert all({"code", "rule"} <= set(rule) for entry in listing for rule in entry["rules"])


def test_unknown_rule_is_a_usage_error():
    # Act / Assert: the library raises, the CLI maps it to exit 2.
    _load_builtin_checks()
    with pytest.raises(UsageError, match="NOPE-999"):
        from lanorme.reference import print_rule_detail

        print_rule_detail(code="NOPE-999")
    assert describe_rule(code="NOPE-999") is None
    assert _run(["rule", "NOPE-999"]) == 2
