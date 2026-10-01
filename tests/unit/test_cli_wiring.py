"""Tests for the CLI config-wiring the unit checks cannot exercise directly.

These lock the parts that live in ``cli.py``: that ``source_root`` is injected
into the layout-aware checks and *only* those, and that a configured
``exclude`` reaches the discovery layer through ``main``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanorme import CheckResult, Registry, Violation, discovery, get_registry
from lanorme.cli import _load_builtin_checks, main
from lanorme.reports import _emit_github
from lanorme.scan import Scan


class _Spy:
    """A throwaway configurable check that records the settings it is handed."""

    name = "spy_check"
    description = "records settings"
    rules: list[str] = []

    def __init__(self) -> None:
        self.received: dict[str, object] | None = None

    def configure(self, *, settings: dict[str, object]) -> None:
        self.received = dict(settings)


def test_source_root_injected_only_into_layout_checks():
    # Arrange: a spy stands in for a generic configurable check.
    _load_builtin_checks()
    registry = Registry({**get_registry(), "spy_check": _Spy()})

    # Act.
    configured = registry.build_configured(
        {
            "source_root": "src/pkg",
            "layer_deps": {"composition_root": ["api/dependencies.py"]},
            "spy_check": {"some_key": 1},
        },
    )

    # Assert: the layout-aware checks receive it; the spy does not.
    assert configured["layer_deps"].source_root == "src/pkg"
    assert configured["port_coverage"].source_root == "src/pkg"
    assert configured["security_patterns"].source_root == "src/pkg"
    assert configured["spy_check"].received == {"some_key": 1}


_OWNER_TABLES = {
    "file_limits": {"file_warn_lines": 150, "file_error_lines": 240},
    "layer_deps": {"layers": ["entities"], "composition_root": ["wiring.py"]},
    "port_coverage": {
        "ports_dir": "core/ports",
        "adapter_roots": ["adapters"],
        "composition_root": ["boot.py"],
    },
}


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("file_warn_lines", 150),
        ("file_error_lines", 240),
        ("layers", ("entities",)),
        ("layer_composition_root", ("wiring.py",)),
        ("ports_dir", "core/ports"),
        ("adapter_roots", ("adapters",)),
        ("port_composition_root", ("boot.py",)),
    ],
)
def test_owner_settings_are_mirrored_into_shallow_modules(key: str, expected: object):
    # Arrange
    _load_builtin_checks()

    # Act
    configured = get_registry().build_configured(_OWNER_TABLES)

    # Assert
    assert getattr(configured["shallow_modules"], key) == expected


def test_a_value_set_in_shallow_modules_wins_over_the_mirror():
    # Arrange
    _load_builtin_checks()
    config = {**_OWNER_TABLES, "shallow_modules": {"file_warn_lines": 210, "layers": ["core"]}}

    # Act
    configured = get_registry().build_configured(config)

    # Assert
    assert configured["shallow_modules"].file_warn_lines == 210
    assert configured["shallow_modules"].layers == ("core",)


def test_a_mistyped_owner_value_is_reported_against_its_owner(tmp_path: Path, capsys):
    # Arrange
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme.layer_deps]\nlayers = "domain"\n',
        encoding="utf-8",
    )
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    # Act
    with pytest.raises(SystemExit) as exit_signal:
        main(["check", str(tmp_path)])
    err = capsys.readouterr().err

    # Assert
    assert exit_signal.value.code == 2
    assert "[tool.lanorme.layer_deps]" in err
    assert "shallow_modules" not in err


@pytest.mark.parametrize("value", [True, 2.5, ["domain", 1]])
def test_a_mistyped_owner_value_is_not_mirrored(value: object):
    # Arrange: shallow_modules alone, so only a mirrored value could reach it;
    # a bool is not an integer here, though Python counts it as one.
    _load_builtin_checks()
    registry = Registry({"shallow_modules": get_registry()["shallow_modules"]})

    # Act
    configured = registry.build_configured(
        {"file_limits": {"file_warn_lines": value}, "layer_deps": {"layers": value}},
    )

    # Assert: the defaults stand; the owner reports its own value in a real run.
    assert configured["shallow_modules"].file_warn_lines == 300
    assert configured["shallow_modules"].layers == (
        "domain",
        "application",
        "infrastructure",
        "api",
    )


def test_only_shallow_modules_receives_mirrored_keys():
    # Arrange: a spy reads every key it is handed.
    _load_builtin_checks()
    registry = Registry({**get_registry(), "spy_check": _Spy()})

    # Act
    configured = registry.build_configured({**_OWNER_TABLES, "spy_check": {"some_key": 1}})

    # Assert
    assert configured["spy_check"].received == {"some_key": 1}
    assert configured["layer_deps"].layers == ("entities",)
    assert configured["file_limits"].file_warn_lines == 150


def test_configured_copies_leave_the_registered_checks_untouched():
    # Arrange
    _load_builtin_checks()
    template = get_registry()["layer_deps"]
    before = template.source_root

    # Act
    configured = get_registry().build_configured({"source_root": "src/pkg"})

    # Assert
    assert configured["layer_deps"] is not template
    assert configured["layer_deps"].source_root == "src/pkg"
    assert template.source_root == before


def test_authn_fires_on_a_src_layout_project_through_the_cli(tmp_path: Path, capsys):
    # Arrange: the reported repro. A src-layout project whose only endpoint is
    # an unauthenticated mutation, declaring source_root the way the docs say.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nsource_root = "src/mypkg"\n',
        encoding="utf-8",
    )
    routers = tmp_path / "src" / "mypkg" / "api" / "routers"
    routers.mkdir(parents=True)
    (routers / "things.py").write_text(
        '@router.put("/things/{thing_id}")\n'
        "async def receive_thing(thing_id: int, payload: dict):\n"
        "    return payload\n",
        encoding="utf-8",
    )

    # Act: the run fails on the finding rather than reporting a clean pass.
    with pytest.raises(SystemExit) as exc:
        main(["check", str(tmp_path), "--check", "security_patterns", "--json"])

    # Assert.
    assert exc.value.code == 1
    results = json.loads(capsys.readouterr().out)
    codes = [v["rule"].split(":", 1)[0] for result in results for v in result["violations"]]
    assert "AUTHN-001" in codes


class _ExcludeSpy:
    """A check that records the exclude globs of the scan it is handed."""

    name = "exclude_spy"
    description = "records the scan's excludes"
    rules: list[str] = []
    seen: list[tuple[str, ...]] = []

    def check(self, scan: Scan) -> CheckResult:
        _ExcludeSpy.seen.append(scan.excludes)
        return CheckResult.from_findings(check=self.name)


def test_main_publishes_configured_excludes_to_discovery(tmp_path: Path, capsys, monkeypatch):
    # Arrange: a project that configures an exclude glob, and a spy check.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nexclude = ["vendor/*"]\n',
        encoding="utf-8",
    )
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _load_builtin_checks()
    monkeypatch.setattr(_ExcludeSpy, "seen", [])
    monkeypatch.setattr("lanorme._registry", Registry({"exclude_spy": _ExcludeSpy()}))

    # Act: run the real CLI entry point (it may exit nonzero on findings).
    try:
        main(["check", str(tmp_path), "--json"])
    except SystemExit:
        pass

    # Assert: the glob reached the scan the check was handed, and did not
    # outlive the run.
    assert _ExcludeSpy.seen and all("vendor/*" in seen for seen in _ExcludeSpy.seen)
    assert "vendor/*" not in discovery.get_active_excludes()


def test_show_config_reports_source_and_opt_in_state(tmp_path: Path, capsys):
    # Arrange: a project with no config (built-in defaults).
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

    # Act: --show-config prints and returns without running checks.
    main(["check", str(tmp_path), "--show-config"])
    out = capsys.readouterr().out

    # Assert: it names the config source and flags an opt-in check as not enabled.
    assert "config file:" in out
    assert "attribute_access" in out
    assert "(opt-in, not enabled)" in out


# --------------------------------------------------------------------------- #
# GitHub annotations output format
# --------------------------------------------------------------------------- #


def _make_result(*, violations=(), warnings=()):
    return CheckResult(check="test_check", violations=list(violations), warnings=list(warnings))


def test_github_format_violations(capsys):
    # Arrange: a single violation.
    v = Violation(file="src/foo.py", line=42, rule="SQL-001", message="raw query", fix="use ORM")

    # Act.
    _emit_github(results=[_make_result(violations=[v])])

    # Assert: emits the ::error workflow command with correct fields.
    out = capsys.readouterr().out
    assert "::error file=src/foo.py,line=42,title=SQL-001::raw query" in out


def test_github_format_title_uses_code_not_full_rule(capsys):
    # Arrange: a real rule string carrying a ': ' description and a comma, which
    # would corrupt the comma-separated, '::'-terminated annotation properties.
    v = Violation(
        file="src/foo.py",
        line=9,
        rule="DRY-001: Near-duplicate function body, refactor",
        message="duplicate of bar()",
        fix="extract a helper",
    )

    # Act.
    _emit_github(results=[_make_result(violations=[v])])

    # Assert: the title is the bare code; the description never reaches the
    # annotation, so no stray property or terminator is introduced.
    out = capsys.readouterr().out
    assert "title=DRY-001::duplicate of bar()" in out
    assert "Near-duplicate" not in out


def test_github_format_escapes_newlines_in_message(capsys):
    # Arrange: a multi-line message, which must not break the single-line command.
    v = Violation(file="a.py", line=1, rule="X-001", message="line one\nline two", fix="f")

    # Act.
    _emit_github(results=[_make_result(violations=[v])])

    # Assert: the newline is encoded, keeping the command on one line.
    out = capsys.readouterr().out
    assert "::error file=a.py,line=1,title=X-001::line one%0Aline two" in out


def test_github_format_warnings(capsys):
    # Arrange: a single warning.
    w = Violation(
        file="src/bar.py",
        line=7,
        rule="CMT-001",
        message="missing docstring",
        fix="add one",
    )

    # Act.
    _emit_github(results=[_make_result(warnings=[w])])

    # Assert: emits the ::warning workflow command with correct fields.
    out = capsys.readouterr().out
    assert "::warning file=src/bar.py,line=7,title=CMT-001::missing docstring" in out


def test_github_format_flag(tmp_path: Path, capsys):
    # Arrange: a minimal project with no findings.
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

    # Act: pass --output-format github explicitly.
    try:
        main(["check", str(tmp_path), "--output-format", "github"])
    except SystemExit:
        pass

    # Assert: no annotation lines emitted for a clean project.
    out = capsys.readouterr().out
    assert "::error" not in out
    assert "::warning" not in out


def test_github_autodetect_via_env(tmp_path: Path, capsys, monkeypatch):
    # Arrange: simulate running inside GitHub Actions.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

    # Act: run without --output-format; should auto-detect the env var.
    try:
        main(["check", str(tmp_path)])
    except SystemExit:
        pass

    # Assert: concise/full-format summary lines must not appear; only annotation
    # lines are valid output under the github format.
    out = capsys.readouterr().out
    assert "All " not in out  # concise summary footer
    assert "Summary:" not in out  # concise summary footer with findings
