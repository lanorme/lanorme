"""``lanorme rule`` and ``lanorme rules``: exact resolution, honest boundaries, JSON forms."""

from __future__ import annotations

import json

import pytest

from lanorme.cli import _load_builtin_checks, main
from lanorme.errors import UsageError
from lanorme.reference import (
    _collect_headings,
    _find_section_bounds,
    describe_rule,
    print_rule_detail,
)


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
    # Arrange.
    _load_builtin_checks()

    # Act.
    detail = describe_rule(code="NOPE-999")
    exit_code = _run(["rule", "NOPE-999"])

    # Assert: the lookup is None, the printer raises, the CLI maps that to exit 2.
    assert detail is None
    with pytest.raises(UsageError, match="NOPE-999"):
        print_rule_detail(code="NOPE-999")
    assert exit_code == 2


# --------------------------------------------------------------------------- #
# a rule that is off until a setting enables it is reported as opt-in
# --------------------------------------------------------------------------- #


def test_rule_behind_a_setting_names_the_setting(capsys):
    # Arrange: PROSE-001 in the comments check is off until em_dash = true.
    _load_builtin_checks()

    # Act.
    _run(["rule", "PROSE-001"])
    out = capsys.readouterr().out
    detail = describe_rule(code="PROSE-001")

    # Assert: not "on by default", the setting named, the JSON carrying both.
    assert "check: comments (opt-in via em_dash = true in [tool.lanorme.comments])" in out
    assert (detail["opt_in"], detail["opt_in_setting"]) == (True, "em_dash")


def test_default_on_rule_of_the_same_check_stays_on_by_default(capsys):
    # Arrange.
    _load_builtin_checks()

    # Act.
    _run(["rule", "CMT-001"])
    out = capsys.readouterr().out
    detail = describe_rule(code="KWARG-001")

    # Assert: CMT-001 is on; a check-level opt-in reports its "enabled" key.
    assert "check: comments (on by default)" in out
    assert (detail["opt_in"], detail["opt_in_setting"]) == (True, "enabled")


def test_opt_in_rule_without_a_declared_setting_is_just_opt_in(monkeypatch, capsys):
    # Arrange: a check that declares opt_in_rules but no opt_in_settings.
    from dataclasses import dataclass, field

    import lanorme

    @dataclass
    class _Gated:
        name: str = "gated"
        description: str = "gated rules"
        opt_in_rules: frozenset[str] = frozenset({"GATE-002"})
        rules: list[str] = field(
            default_factory=lambda: ["GATE-001: always", "GATE-002: sometimes"],
        )

        def run(self, *, src_root: str):
            return lanorme.CheckResult.from_findings(check=self.name)

    monkeypatch.setattr(lanorme, "_registry", {"gated": _Gated()})

    # Act.
    _run(["rule", "GATE-002"])
    gated = capsys.readouterr().out
    _run(["rules", "--json"])
    listing = json.loads(capsys.readouterr().out)

    # Assert: the bare "opt-in" marker, and the listing flags the rule, not the check.
    assert "check: gated (opt-in)" in gated
    assert listing[0]["opt_in"] is False
    assert [rule["opt_in"] for rule in listing[0]["rules"]] == [False, True]
