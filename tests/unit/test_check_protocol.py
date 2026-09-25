"""The check entry point: ``check(scan)``, and the deprecated ``run(*, src_root)``.

``run_check`` hands a check the scan it runs over and keeps it active for the
call; a check that only defines ``run`` still runs, under the same scan, with
one ``DeprecationWarning`` per check class. An exception out of either entry
point becomes a ``RUN-000`` notice rather than sinking the run.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import lanorme
from lanorme import CheckResult, Registry, Violation, run_all, run_check
from lanorme.cli import main
from lanorme.scan import Scan, get_current_scan


@dataclass
class _ScanSpy:
    """A check that records the scan it is handed and the one active meanwhile."""

    name: str = "scan_spy"
    description: str = "records its scan"
    rules: list[str] = field(default_factory=lambda: ["SPY-001: seen"])
    handed: Scan | None = None
    active: Scan | None = None

    def check(self, scan: Scan) -> CheckResult:
        self.handed = scan
        self.active = get_current_scan()
        return CheckResult.from_findings(check=self.name)


def _build_legacy_class() -> type:
    """A fresh run-only check class, so the once-per-class warning starts afresh."""

    @dataclass
    class _Legacy:
        name: str = "legacy"
        description: str = "still defines run"
        rules: list[str] = field(default_factory=lambda: ["OLD-001: Say it once"])
        seen_root: str = ""
        seen_excludes: tuple[str, ...] = ()

        def run(self, *, src_root: str) -> CheckResult:
            self.seen_root = src_root
            self.seen_excludes = get_current_scan().excludes
            finding = Violation(file="f.py", line=1, rule="OLD-001", message="m", fix="x")
            return CheckResult.from_findings(check=self.name, violations=[finding])

    return _Legacy


def _record_run_deprecations(action) -> list[str]:
    """The ``run(*, src_root)`` deprecation messages *action* emits."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        action()
    return [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning) and "run(*, src_root)" in str(w.message)
    ]


# --------------------------------------------------------------------------- #
# the check(scan) entry point
# --------------------------------------------------------------------------- #


def test_run_check_hands_the_check_its_scan_and_keeps_it_active_for_the_call(tmp_path: Path):
    # Arrange
    spy = _ScanSpy()
    scan = Scan(root=tmp_path, excludes=("vendor/*",), source_root="src/pkg")
    before = get_current_scan()

    # Act
    run_check(spy, scan=scan)

    # Assert: the same scan is handed and current while the check runs, not after.
    assert spy.handed == scan
    assert spy.active == scan
    assert get_current_scan() == before


def test_run_check_with_a_bare_path_scans_that_root_under_the_confinement_in_force(
    tmp_path: Path,
):
    # Arrange: a run confined by excludes is active, as under the CLI.
    spy = _ScanSpy()

    # Act
    with Scan(root=tmp_path / "elsewhere", excludes=("vendor/*",)).activate():
        run_check(spy, src_root=str(tmp_path))

    # Assert: rooted where asked, pruning what the run prunes.
    assert spy.handed is not None
    assert spy.handed.root == tmp_path
    assert spy.handed.excludes == ("vendor/*",)


def test_run_check_needs_a_scan_or_a_path():
    # Arrange / Act / Assert
    with pytest.raises(TypeError, match="scan="):
        run_check(_ScanSpy())


def test_run_all_hands_every_check_one_scan(monkeypatch, tmp_path: Path):
    # Arrange
    first, second = _ScanSpy(name="first"), _ScanSpy(name="second")
    monkeypatch.setattr(lanorme, "_registry", Registry({"first": first, "second": second}))
    scan = Scan(root=tmp_path, excludes=("build/*",))

    # Act
    results = run_all(scan=scan)

    # Assert
    assert [r.check for r in results] == ["first", "second"]
    assert first.handed == scan
    assert second.handed == scan


def test_an_exception_out_of_check_becomes_a_crash_notice(tmp_path: Path):
    # Arrange
    @dataclass
    class _Broken(_ScanSpy):
        def check(self, scan: Scan) -> CheckResult:
            raise RecursionError("too deep")

    # Act
    result = run_check(_Broken(name="broken"), scan=Scan(root=tmp_path))

    # Assert: the run goes on, with the failure named on that check.
    assert result.check == "broken"
    assert [w.code for w in result.warnings] == ["RUN-000"]
    assert "RecursionError: too deep" in result.warnings[0].message


# --------------------------------------------------------------------------- #
# the deprecated run(*, src_root)
# --------------------------------------------------------------------------- #


def test_a_run_only_check_still_runs_under_the_scan_and_is_told_it_is_deprecated(
    tmp_path: Path,
):
    # Arrange
    legacy = _build_legacy_class()()
    scan = Scan(root=tmp_path, excludes=("vendor/*",))

    # Act
    with pytest.warns(DeprecationWarning, match=r"run\(\*, src_root\).*deprecated"):
        result = run_check(legacy, scan=scan)

    # Assert: called with the scan's root, under its excludes, findings expanded as usual.
    assert legacy.seen_root == str(tmp_path)
    assert legacy.seen_excludes == ("vendor/*",)
    assert [v.rule for v in result.violations] == ["OLD-001: Say it once"]


def test_the_run_deprecation_is_said_once_per_check_class(tmp_path: Path):
    # Arrange: two instances of one class, then a second class.
    one_class = _build_legacy_class()
    another_class = _build_legacy_class()
    scan = Scan(root=tmp_path)

    # Act
    first_class_twice = _record_run_deprecations(
        lambda: (run_check(one_class(), scan=scan), run_check(one_class(), scan=scan)),
    )
    other_class = _record_run_deprecations(lambda: run_check(another_class(), scan=scan))

    # Assert: a run over many regions warns once per class, and each class once.
    assert len(first_class_twice) == 1
    assert len(other_class) == 1


def test_a_run_only_plugin_still_reports_through_the_cli(tmp_path: Path, capsys, monkeypatch):
    # Arrange: a plugin module written against the previous protocol.
    (tmp_path / "old_plugin.py").write_text(
        "from lanorme import CheckResult, Violation, register\n\n"
        "class Old:\n"
        "    name = 'old'\n    description = 'old protocol'\n"
        "    rules = ['OLD-001: rule']\n"
        "    def run(self, *, src_root):\n"
        "        v = Violation(file='mod.py', line=1, rule='OLD-001', message='m', fix='f')\n"
        "        return CheckResult.from_findings(check=self.name, violations=[v])\n\n"
        "register(Old())\n",
        encoding="utf-8",
    )
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(lanorme, "_registry", Registry())

    # Act
    with pytest.warns(DeprecationWarning, match="old_plugin.Old"):
        try:
            main(["check", str(tmp_path), "--plugin", "old_plugin", "--output-format", "ndjson"])
        except SystemExit:
            pass
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]

    # Assert: its finding reaches the output like any other check's.
    assert [(r["check"], r["code"], r["file"]) for r in records] == [("old", "OLD-001", "mod.py")]
