"""Tests for ``extends`` profiles: bundled presets merged under local config.

``[tool.lanorme] extends = ["strict"]`` adopts a bundled profile (or a local
``.toml`` path); profiles merge left to right and the local config always wins.

Every test goes through the CLI: a profile is only as real as the findings it
changes, and ``--show-config`` shows the merged settings it produced.
"""

from __future__ import annotations

import json
from importlib.resources import files as resource_files
from pathlib import Path

import pytest

from lanorme import get_all_checks
from lanorme.cli import _load_builtin_checks, main

# Default-off checks the strict profile leaves off on purpose. Empty today: a
# name goes here only with its reason, so an omission is a decision, not drift.
_STRICT_LEAVES_OFF: frozenset[str] = frozenset()

_STRICT = '[tool.lanorme]\nextends = ["strict"]\n'
# KWARG-001 (named_args, default-off) and a PARAM-001 warning (5 params), both on line 3.
_OPT_IN_AND_WARNING = "\n\ndef transfer(amount, currency, a, b, c):\n    return amount\n"
# An EVAL-001 error on line 2.
_EVAL = "def f(x):\n    return eval(x)\n"


def _run(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


def _read_findings(capsys) -> set[tuple[str, str, int, str]]:
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return {(r["code"], r["file"], r["line"], r["severity"]) for r in records}


def _write_project(root: Path, *, config: str = "[tool.lanorme]\n", **files: str) -> None:
    (root / "pyproject.toml").write_text(config, encoding="utf-8")
    for name, body in files.items():
        (root / f"{name}.py").write_text(body, encoding="utf-8")


def _list_bundled_profiles() -> list[str]:
    """Names of the profiles shipped as package data under ``lanorme/profiles``."""
    directory = resource_files("lanorme") / "profiles"
    return sorted(
        entry.name[: -len(".toml")] for entry in directory.iterdir() if entry.name.endswith(".toml")
    )


def _default_off_checks() -> set[str]:
    """Names of the registered checks whose ``enabled`` switch ships off.

    Read from a fresh instance rather than the live singleton: an earlier
    ``main([...])`` in the same process may already have configured it.
    """
    _load_builtin_checks()
    return {
        name
        for name, check in get_all_checks().items()
        if getattr(type(check)(), "enabled", None) is False
    }


def _read_enabled_switches(out: str) -> dict[str, bool]:
    """The ``enabled`` switch of every check in a ``--show-config`` dump."""
    listing = out.split("checks (effective settings):", 1)[1]
    switches: dict[str, bool] = {}
    for line in listing.splitlines():
        name, _sep, settings = line.strip().partition(" ")
        if "enabled=" in settings:
            switches[name] = "enabled=True" in settings
    return switches


def test_strict_is_a_bundled_profile():
    # Assert: the shipped profile is discoverable by name.
    assert "strict" in _list_bundled_profiles()


@pytest.mark.parametrize("name", _list_bundled_profiles())
def test_every_bundled_profile_loads_through_extends(tmp_path: Path, name: str):
    # Arrange
    _write_project(tmp_path, config=f'[tool.lanorme]\nextends = ["{name}"]\n', m="x = 1\n")

    # Act
    code = _run(["check", str(tmp_path)])

    # Assert: invalid TOML or a table naming no check would be a config error.
    assert code != 2


def test_extends_strict_turns_on_default_off_checks_and_promotes(tmp_path: Path, capsys):
    # Arrange: KWARG-001 is default-off; PARAM-001 at 5 params is a warning.
    _write_project(tmp_path, config=_STRICT, m=_OPT_IN_AND_WARNING)

    # Act
    code = _run(["check", str(tmp_path), "--output-format", "ndjson"])
    found = _read_findings(capsys)

    # Assert: the opt-in check fires and the warning is promoted.
    assert {("KWARG-001", "m.py", 3, "error"), ("PARAM-001", "m.py", 3, "error")} <= found
    assert code == 1


def test_strict_enables_every_default_off_check(tmp_path: Path, capsys):
    # Arrange
    default_off = _default_off_checks()
    _write_project(tmp_path, config=_STRICT, m="x = 1\n")

    # Act
    _run(["check", str(tmp_path), "--show-config"])
    switches = _read_enabled_switches(capsys.readouterr().out)

    # Assert: a listed exclusion must still name a real default-off check, and
    # strict enables exactly the rest, so a new default-off check that is not
    # added to the profile (or to the exclusion list) fails here.
    assert _STRICT_LEAVES_OFF <= default_off
    assert {name for name in default_off if switches[name]} == default_off - _STRICT_LEAVES_OFF


def test_strict_prices_a_blanket_suppression(tmp_path: Path, capsys):
    # Arrange: a project on strict with one bare ``noqa`` comment, which the
    # suppressions check (default-off, on under strict) flags as SUPPRESS-002.
    _write_project(tmp_path, config=_STRICT, m="x = 1  # noqa\n")

    # Act
    _run(["check", str(tmp_path), "--check", "suppressions", "--output-format", "ndjson"])

    # Assert: the directive on the line cannot silence its own pricing.
    assert ("SUPPRESS-002", "m.py", 1, "error") in _read_findings(capsys)


def test_local_table_switches_one_strict_check_back_off(tmp_path: Path, capsys):
    # Arrange: the project keeps strict but opts out of one check it enables.
    _write_project(
        tmp_path,
        config=_STRICT + "\n[tool.lanorme.named_args]\nenabled = false\n",
        m=_OPT_IN_AND_WARNING,
    )

    # Act
    _run(["check", str(tmp_path), "--output-format", "ndjson"])
    found = _read_findings(capsys)

    # Assert: only the named check is off; tables merge key by key, so the
    # rest of what strict sets (the promotion) comes through untouched.
    assert not [f for f in found if f[0] == "KWARG-001"]
    assert ("PARAM-001", "m.py", 3, "error") in found


def test_local_promote_overrides_the_profile(tmp_path: Path, capsys):
    # Arrange: the project keeps strict's opt-ins but opts out of promotion.
    _write_project(tmp_path, config=_STRICT + "promote = []\n", m=_OPT_IN_AND_WARNING)

    # Act
    _run(["check", str(tmp_path), "--output-format", "ndjson"])
    found = _read_findings(capsys)

    # Assert: the local promote wins; the enabled opt-in still fires.
    assert ("KWARG-001", "m.py", 3, "error") in found
    assert ("PARAM-001", "m.py", 3, "warning") in found


def test_extends_accepts_a_local_toml_path(tmp_path: Path, capsys):
    # Arrange: a house profile that ignores the one rule the file breaks.
    (tmp_path / "house.toml").write_text('ignore = ["EVAL-001"]\n', encoding="utf-8")
    _write_project(tmp_path, config='[tool.lanorme]\nextends = ["house.toml"]\n', m=_EVAL)

    # Act
    code = _run(["check", str(tmp_path), "--check", "EVAL-001", "--output-format", "ndjson"])

    # Assert
    assert _read_findings(capsys) == set()
    assert code == 0


def test_later_profile_wins_when_composing(tmp_path: Path, capsys):
    # Arrange: two local profiles set ``select``; the second must win.
    (tmp_path / "a.toml").write_text('select = ["SIZE"]\n', encoding="utf-8")
    (tmp_path / "b.toml").write_text('select = ["EVAL-001"]\n', encoding="utf-8")
    _write_project(tmp_path, config='[tool.lanorme]\nextends = ["a.toml", "b.toml"]\n', m=_EVAL)

    # Act
    _run(["check", str(tmp_path), "--output-format", "ndjson"])

    # Assert: EVAL-001 is selected, which only b.toml does.
    assert _read_findings(capsys) == {("EVAL-001", "m.py", 2, "error")}


def test_unknown_profile_is_a_usage_error(tmp_path: Path, capsys):
    # Arrange
    _write_project(tmp_path, config='[tool.lanorme]\nextends = ["does-not-exist"]\n', m="x = 1\n")

    # Act
    code = _run(["check", str(tmp_path)])

    # Assert
    assert code == 2
    assert "unknown profile 'does-not-exist'" in capsys.readouterr().err


# --- red-team regressions: malformed `extends`, malformed profiles, region cascade ---


def test_extends_as_a_table_is_rejected(tmp_path: Path, capsys):
    # Arrange: a TOML `[extends]` table parses to a dict, which is malformed.
    _write_project(tmp_path, config="[tool.lanorme.extends]\nstrict = true\n", m="x = 1\n")

    # Act
    code = _run(["check", str(tmp_path)])

    # Assert: rejected, not silently iterated into ['strict'].
    assert code == 2
    assert "'extends' must be" in capsys.readouterr().err


def test_extends_as_a_scalar_is_rejected(tmp_path: Path, capsys):
    # Arrange
    _write_project(tmp_path, config="[tool.lanorme]\nextends = 5\n", m="x = 1\n")

    # Act
    code = _run(["check", str(tmp_path)])

    # Assert: a usage error naming the type, not a TypeError.
    assert code == 2
    assert "got int" in capsys.readouterr().err


def test_malformed_profile_toml_exits_cleanly(tmp_path: Path, capsys):
    # Arrange
    (tmp_path / "bad.toml").write_text("this is = = not toml\n", encoding="utf-8")
    _write_project(tmp_path, config='[tool.lanorme]\nextends = ["bad.toml"]\n', m="x = 1\n")

    # Act
    code = _run(["check", str(tmp_path)])

    # Assert: a usage error naming the file, not a raw TOMLDecodeError.
    assert code == 2
    assert "bad.toml' is not valid TOML" in capsys.readouterr().err


def test_hexagonal_exempts_a_package_form_composition_root(tmp_path: Path, capsys):
    # Arrange: the wiring lives in api/dependencies/__init__.py (package form), which
    # must be exempt from the layer rule when it reaches into infrastructure.
    (tmp_path / "lanorme.toml").write_text('extends = ["hexagonal"]\n', encoding="utf-8")
    for sub in ("domain", "application/ports", "infrastructure", "api/dependencies"):
        (tmp_path / sub).mkdir(parents=True)
    (tmp_path / "domain" / "m.py").write_text("class M:\n    pass\n", encoding="utf-8")
    (tmp_path / "infrastructure" / "repo.py").write_text("class R:\n    pass\n", encoding="utf-8")
    (tmp_path / "api" / "dependencies" / "__init__.py").write_text(
        "from infrastructure.repo import R\n\nr = R()\n",
        encoding="utf-8",
    )

    # Act: a clean run means no SystemExit (no LAYER-005 false positive).
    main(["check", str(tmp_path), "--check", "layer_deps", "--json"])


def test_nested_region_extends_enables_a_file_level_check(tmp_path: Path, capsys):
    # Arrange: a subtree adopts strict, which enables the opt-in attribute_access check.
    (tmp_path / "pyproject.toml").write_text("[tool.lanorme]\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "lanorme.toml").write_text('extends = ["strict"]\n', encoding="utf-8")
    (tmp_path / "sub" / "m.py").write_text(
        'def f(o):\n    return hasattr(o, "x")\n',
        encoding="utf-8",
    )

    # Act
    _run(["check", str(tmp_path), "--output-format", "ndjson"])

    # Assert: ATTR-001 fired for the subtree, so the nested extends was resolved.
    assert ("ATTR-001", "sub/m.py", 2) in {(c, f, ln) for c, f, ln, _ in _read_findings(capsys)}
