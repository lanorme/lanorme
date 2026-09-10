"""Tests for ``extends`` profiles: bundled presets merged under local config.

``[tool.lanorme] extends = ["strict"]`` adopts a bundled profile (or a local
``.toml`` path); profiles merge left to right and the local config always wins.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from lanorme import get_all_checks
from lanorme.cli import _load_builtin_checks, main
from lanorme.presets import _bundled_profiles, _load_profile, _resolve_extends

# Default-off checks the strict profile leaves off on purpose. Empty today: a
# name goes here only with its reason, so an omission is a decision, not drift.
_STRICT_LEAVES_OFF: frozenset[str] = frozenset()


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


def _switched_on_by_strict() -> set[str]:
    """Names of the tables the strict profile sets ``enabled = true`` on."""
    profile = _load_profile(name="strict", project_root=Path("/nonexistent"))
    return {
        name
        for name, table in profile.items()
        if isinstance(table, dict) and table.get("enabled") is True
    }


def test_strict_is_a_bundled_profile():
    # Assert: the shipped profile is discoverable by name.
    assert "strict" in _bundled_profiles()


@pytest.mark.parametrize("name", _bundled_profiles())
def test_every_bundled_profile_is_valid_toml(name: str):
    # Act: each bundled profile loads without raising.
    profile = _load_profile(name=name, project_root=Path("/nonexistent"))

    # Assert.
    assert isinstance(profile, dict)


def test_strict_enables_opt_ins_and_promotes_all():
    # Act.
    merged = _resolve_extends(config={"extends": ["strict"]}, project_root=Path("."))

    # Assert: opt-in checks switched on and every warning promoted.
    assert merged["promote"] == ["ALL"]
    assert merged["named_args"]["enabled"] is True
    assert merged["prose"]["enabled"] is True


def test_strict_enables_every_default_off_check():
    # Arrange.
    default_off = _default_off_checks()

    # Assert: a listed exclusion must still name a real default-off check, and
    # strict enables exactly the rest, so a new default-off check that is not
    # added to the profile (or to the exclusion list) fails here.
    assert _STRICT_LEAVES_OFF <= default_off
    assert _switched_on_by_strict() == default_off - _STRICT_LEAVES_OFF


def test_every_strict_table_names_a_registered_check():
    # Arrange: a misspelt table would merge silently and configure nothing.
    _load_builtin_checks()
    profile = _load_profile(name="strict", project_root=Path("/nonexistent"))
    tables = {name for name, value in profile.items() if isinstance(value, dict)}

    # Assert.
    assert tables <= set(get_all_checks())


def test_strict_prices_a_blanket_suppression(tmp_path: Path, capsys):
    # Arrange: a project on strict with one bare ``# noqa``, which the
    # suppressions check (default-off, on under strict) flags as SUPPRESS-002.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nextends = ["strict"]\n', encoding="utf-8"
    )
    (tmp_path / "m.py").write_text("x = 1  # noqa\n", encoding="utf-8")

    # Act.
    try:
        main(["check", str(tmp_path), "--check", "suppressions", "--json"])
    except SystemExit:
        pass

    # Assert.
    assert "SUPPRESS-002" in capsys.readouterr().out


def test_local_table_switches_one_strict_check_back_off():
    # Arrange: the project keeps strict but opts out of one check it enables.
    config = {"extends": ["strict"], "docstrings": {"enabled": False}}

    # Act.
    merged = _resolve_extends(config=config, project_root=Path("."))

    # Assert: only the named check is off; tables merge key by key, so the
    # rest of what strict enables comes through untouched.
    assert merged["docstrings"]["enabled"] is False
    assert merged["suppressions"]["enabled"] is True


def test_local_config_overrides_the_profile():
    # Arrange: the project keeps strict's opt-ins but opts out of promotion.
    config = {"extends": ["strict"], "promote": []}

    # Act.
    merged = _resolve_extends(config=config, project_root=Path("."))

    # Assert: the local promote wins; the enabled opt-ins still come through.
    assert merged["promote"] == []
    assert merged["named_args"]["enabled"] is True
    assert "extends" not in merged


def test_extends_accepts_a_local_toml_path(tmp_path: Path):
    # Arrange.
    (tmp_path / "house.toml").write_text('ignore = ["NAMING-003"]\n', encoding="utf-8")

    # Act.
    merged = _resolve_extends(config={"extends": ["house.toml"]}, project_root=tmp_path)

    # Assert.
    assert merged["ignore"] == ["NAMING-003"]


def test_later_profile_wins_when_composing(tmp_path: Path):
    # Arrange: two local profiles set the same key; the second must win.
    (tmp_path / "a.toml").write_text('select = ["TYPE"]\n', encoding="utf-8")
    (tmp_path / "b.toml").write_text('select = ["DRY"]\n', encoding="utf-8")

    # Act.
    merged = _resolve_extends(config={"extends": ["a.toml", "b.toml"]}, project_root=tmp_path)

    # Assert.
    assert merged["select"] == ["DRY"]


def test_no_extends_is_returned_unchanged():
    # Arrange / Act.
    config = {"ignore": ["NAMING-003"]}
    merged = _resolve_extends(config=config, project_root=Path("."))

    # Assert.
    assert merged is config


def test_unknown_profile_exits_two():
    # Act / Assert.
    with pytest.raises(SystemExit) as exc:
        _load_profile(name="does-not-exist", project_root=Path("."))
    assert exc.value.code == 2


# --- red-team regressions: malformed `extends`, malformed profiles, region cascade ---


def test_extends_as_a_table_is_rejected():
    # Arrange: a TOML `[extends]` table parses to a dict, which is malformed.
    config = {"extends": {"strict": True}}

    # Act / Assert: rejected, not silently iterated into ['strict'].
    with pytest.raises(SystemExit) as exc:
        _resolve_extends(config=config, project_root=Path("."))
    assert exc.value.code == 2


def test_extends_as_a_scalar_is_rejected():
    # Act / Assert: a bare scalar must exit cleanly, not raise TypeError.
    with pytest.raises(SystemExit) as exc:
        _resolve_extends(config={"extends": 5}, project_root=Path("."))
    assert exc.value.code == 2


def test_malformed_profile_toml_exits_cleanly(tmp_path: Path):
    # Arrange.
    (tmp_path / "bad.toml").write_text("this is = = not toml\n", encoding="utf-8")

    # Act / Assert: a clean exit 2, not a raw TOMLDecodeError traceback.
    with pytest.raises(SystemExit) as exc:
        _load_profile(name="bad.toml", project_root=tmp_path)
    assert exc.value.code == 2


def test_hexagonal_exempts_a_package_form_composition_root(tmp_path: Path, capsys):
    # Arrange: the wiring lives in api/dependencies/__init__.py (package form), which
    # must be exempt from the layer rule when it reaches into infrastructure.
    (tmp_path / "lanorme.toml").write_text('extends = ["hexagonal"]\n', encoding="utf-8")
    for sub in ("domain", "application/ports", "infrastructure", "api/dependencies"):
        (tmp_path / sub).mkdir(parents=True)
    (tmp_path / "domain" / "m.py").write_text("class M:\n    pass\n", encoding="utf-8")
    (tmp_path / "infrastructure" / "repo.py").write_text("class R:\n    pass\n", encoding="utf-8")
    (tmp_path / "api" / "dependencies" / "__init__.py").write_text(
        "from infrastructure.repo import R\n\nr = R()\n", encoding="utf-8"
    )

    # Act: a clean run means no SystemExit (no LAYER-005 false positive).
    main(["check", str(tmp_path), "--check", "layer_deps", "--json"])


def test_nested_region_extends_enables_a_file_level_check(tmp_path: Path, capsys):
    # Arrange: a subtree adopts strict, which enables the opt-in attribute_access check.
    (tmp_path / "pyproject.toml").write_text("[tool.lanorme]\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "lanorme.toml").write_text('extends = ["strict"]\n', encoding="utf-8")
    (tmp_path / "sub" / "m.py").write_text(
        'def f(o):\n    return hasattr(o, "x")\n', encoding="utf-8"
    )

    # Act.
    try:
        main(["check", str(tmp_path), "--json"])
    except SystemExit:
        pass

    # Assert: ATTR-001 fired for the subtree, so the nested extends was resolved.
    assert "ATTR-001" in capsys.readouterr().out
