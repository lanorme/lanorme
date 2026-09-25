"""Tests for the scan context (``lanorme.scan``) and the runner's dispatch.

A ``Scan`` carries the root, the excludes and a shared source cache; the
runner activates it around each check and calls ``check(scan)``, falling back
to the deprecated ``run(src_root=...)`` with a once-per-class warning.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import lanorme
from lanorme import CheckResult, Status, discovery, invoke_check, run_check, run_via_check
from lanorme.scan import Scan, SourceCache, activate, current_scan


def _make_tree(root: Path) -> None:
    for rel in ("pkg/a.py", "vendor/b.py", "docs/page.md", "build/x.py"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")


def _relative(paths: list[Path], root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in paths}


# --- Scan: file walk -------------------------------------------------------- #


def test_files_honour_the_scans_own_excludes_not_the_process_globals():
    # Arrange: the process-wide globs say one thing, the scan another.
    discovery.set_excludes(("pkg/*",))
    try:
        scan = Scan(root=Path("."), excludes=("vendor/*",))
        # Act / Assert: the scan's globs are what it prunes by.
        assert scan.excludes == ("vendor/*",)
    finally:
        discovery.set_excludes(())


def test_files_and_py_files_prune_defaults_and_excludes(tmp_path: Path):
    # Arrange
    _make_tree(tmp_path)
    scan = Scan(root=tmp_path, excludes=("vendor/*",))

    # Act
    everything = _relative(scan.files(), tmp_path)
    python = _relative(scan.py_files(), tmp_path)
    markdown = _relative(scan.files(suffix=".md"), tmp_path)

    # Assert: build/ is pruned by default, vendor/ by the scan's glob.
    assert everything == {"pkg/a.py", "docs/page.md"}
    assert python == {"pkg/a.py"}
    assert markdown == {"docs/page.md"}


def test_for_root_takes_the_excludes_in_effect(tmp_path: Path):
    # Arrange
    _make_tree(tmp_path)
    discovery.set_excludes(("vendor/*",))

    # Act
    try:
        scan = Scan.for_root(str(tmp_path))
    finally:
        discovery.set_excludes(())

    # Assert: the deprecated run(src_root) path sees what set_excludes published.
    assert scan.root == tmp_path
    assert scan.excludes == ("vendor/*",)


def test_relative_reports_root_relative_posix_paths(tmp_path: Path):
    # Arrange
    _make_tree(tmp_path)
    scan = Scan(root=tmp_path)

    # Act / Assert
    assert scan.relative(tmp_path / "pkg" / "a.py") == "pkg/a.py"


# --- Scan: source cache ----------------------------------------------------- #


def test_source_and_module_are_read_and_parsed_once(tmp_path: Path):
    # Arrange
    path = tmp_path / "m.py"
    path.write_text("x = 1\n", encoding="utf-8")
    scan = Scan(root=tmp_path)

    # Act: the file changes on disk between two reads through the cache.
    first_text = scan.source(path)
    first_tree = scan.module(path)
    path.write_text("y = 2\n", encoding="utf-8")
    second_text = scan.source(path)
    second_tree = scan.module(path)

    # Assert: the cached read and parse are served, and the tree is the same object.
    assert first_text == second_text == "x = 1\n"
    assert first_tree is second_tree


def test_cache_is_shared_between_scans_that_are_handed_it(tmp_path: Path):
    # Arrange
    path = tmp_path / "m.py"
    path.write_text("x = 1\n", encoding="utf-8")
    cache = SourceCache()
    tree_scan = Scan(root=tmp_path, cache=cache)
    region_scan = Scan(root=tmp_path, scope="file", cache=cache)

    # Act
    from_tree = tree_scan.module(path)
    from_region = region_scan.module(path)

    # Assert
    assert from_tree is from_region


def test_failed_read_is_not_cached_and_raises(tmp_path: Path):
    # Arrange
    scan = Scan(root=tmp_path)
    missing = tmp_path / "missing.py"

    # Act / Assert: the caller sees the same error it would reading the file.
    with pytest.raises(OSError):
        scan.source(missing)
    with pytest.raises(OSError):
        scan.module(missing)


def test_parsed_modules_yield_parseable_files_and_skip_the_rest(tmp_path: Path):
    # Arrange: one good module, one syntax error, one BOM-prefixed module.
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbfy = 2\n")
    scan = Scan(root=tmp_path)

    # Act
    found = dict(scan.parsed_modules())

    # Assert: sorted by path, the broken file skipped, the BOM honoured.
    assert list(found) == ["bom.py", "good.py"]
    assert found["bom.py"].body[0].targets[0].id == "y"


# --- Activation ------------------------------------------------------------- #


def test_activate_makes_the_scan_current_and_scopes_discovery_excludes(tmp_path: Path):
    # Arrange
    _make_tree(tmp_path)
    scan = Scan(root=tmp_path, excludes=("vendor/*",))
    assert current_scan() is None

    # Act
    with activate(scan) as active:
        inside = current_scan()
        walked = _relative(discovery.iter_py_files(tmp_path), tmp_path)
        in_effect = discovery.active_excludes()

    # Assert: inside the block the bare-root helper prunes what the scan prunes; after, it is restored.
    assert active is scan and inside is scan
    assert walked == {"pkg/a.py"}
    assert in_effect == ("vendor/*",)
    assert current_scan() is None
    assert discovery.active_excludes() == ()


# --- Runner dispatch -------------------------------------------------------- #


class _ModernCheck:
    name = "modern"
    description = "implements check(scan)"
    rules = ["MOD-001: r"]

    def __init__(self) -> None:
        self.seen: Scan | None = None

    def check(self, scan: Scan) -> CheckResult:
        self.seen = scan
        return CheckResult(check=self.name)

    def run(self, *, src_root: str) -> CheckResult:
        raise AssertionError("run() must not be called when check() exists")


class _LegacyCheck:
    name = "legacy"
    description = "implements only run(src_root)"
    rules = ["LEG-001: r"]

    def __init__(self) -> None:
        self.seen_root: str | None = None
        self.seen_excludes: tuple[str, ...] | None = None

    def run(self, *, src_root: str) -> CheckResult:
        self.seen_root = src_root
        self.seen_excludes = discovery.active_excludes()
        return CheckResult(check=self.name)


class _Wrapped:
    """A built-in style check: ``run`` is the thin wrapper over ``check``."""

    name = "wrapped"
    description = "run wraps check"
    rules = ["WRP-001: r"]

    def run(self, *, src_root: str) -> CheckResult:
        return run_via_check(self, src_root=src_root)

    def check(self, scan: Scan) -> CheckResult:
        return CheckResult(check=self.name)


class _Crashing:
    name = "crashing"
    description = "raises"
    rules = ["CRASH-001: r"]

    def check(self, scan: Scan) -> CheckResult:
        raise RecursionError("boom")


def test_run_check_prefers_check_over_run_and_hands_it_the_scan(tmp_path: Path):
    # Arrange
    check = _ModernCheck()
    scan = Scan(root=tmp_path)

    # Act
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = run_check(check, scan=scan)

    # Assert
    assert result.status is Status.PASS
    assert check.seen is scan


def test_run_check_falls_back_to_run_with_the_scan_active_and_warns_once_per_class(tmp_path: Path):
    # Arrange
    scan = Scan(root=tmp_path, excludes=("vendor/*",))
    lanorme._legacy_run_warned.discard(_LegacyCheck)
    first, second = _LegacyCheck(), _LegacyCheck()

    # Act: two instances of the same class, run twice.
    with pytest.warns(DeprecationWarning, match="defines run\\(\\*, src_root\\) but no check\\(scan\\)"):
        run_check(first, scan=scan)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run_check(second, scan=scan)

    # Assert: run() got the root as a string with the scan's excludes in effect.
    assert first.seen_root == str(tmp_path)
    assert first.seen_excludes == ("vendor/*",)
    assert second.seen_root == str(tmp_path)


def test_run_check_from_src_root_builds_a_scan_of_that_path(tmp_path: Path):
    # Arrange
    check = _ModernCheck()

    # Act
    run_check(check, src_root=str(tmp_path))

    # Assert
    assert check.seen is not None
    assert check.seen.root == tmp_path


def test_run_check_needs_a_scan_or_a_src_root():
    # Arrange / Act / Assert
    with pytest.raises(TypeError):
        run_check(_ModernCheck())


def test_run_check_isolates_an_exception_as_a_run000_warning(tmp_path: Path):
    # Arrange / Act
    result = run_check(_Crashing(), scan=Scan(root=tmp_path))

    # Assert
    assert result.status is Status.WARN
    assert result.warnings[0].code == "RUN-000"
    assert "RecursionError" in result.warnings[0].message


def test_invoke_check_lets_an_exception_escape(tmp_path: Path):
    # Arrange / Act / Assert
    with pytest.raises(RecursionError):
        invoke_check(_Crashing(), scan=Scan(root=tmp_path))


def test_run_via_check_warns_once_per_class_and_delegates_to_check(tmp_path: Path):
    # Arrange
    lanorme._legacy_run_warned.discard(_Wrapped)

    # Act
    with pytest.warns(DeprecationWarning, match="_Wrapped.run\\(src_root=...\\) is deprecated"):
        first = _Wrapped().run(src_root=str(tmp_path))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        second = _Wrapped().run(src_root=str(tmp_path))

    # Assert
    assert first.check == "wrapped"
    assert second.check == "wrapped"
