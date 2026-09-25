"""The run context: :class:`Scan`, the check :class:`Registry` and configured copies.

A run's confinement (exclude globs, subtree scope) and parse cache used to be
process-global, and the checks were configured in place and reset between
regions by clearing their ``__dict__``. Now a scan is activated for a block
and restored after it, and each pass runs configured deep copies of the
registered checks. These pin that nothing outlives its block or its pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lanorme import CheckResult, Registry, discovery, sources
from lanorme.cli import main
from lanorme.discovery import iter_py_files
from lanorme.errors import UsageError
from lanorme.scan import Scan, get_current_scan


def _write_tree(root: Path) -> None:
    for relative in ("keep/a.py", "vendor/b.py", "tests/c.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")


def _list_relative(root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in iter_py_files(root)]


@dataclass
class _Threshold:
    """A configurable check built with a constructor argument and no default."""

    limit: int
    name: str = "threshold"
    description: str = "has a limit"
    rules: list[str] = field(default_factory=lambda: ["LIMIT-001: over the limit"])

    def configure(self, *, settings: dict[str, object]) -> None:
        if "limit" in settings:
            self.limit = int(settings["limit"])

    def run(self, *, src_root: str) -> CheckResult:
        return CheckResult.from_findings(check=self.name)


def test_an_active_scan_confines_the_walk_only_inside_its_block(tmp_path: Path) -> None:
    # Arrange
    _write_tree(tmp_path)
    scan = Scan(root=tmp_path, excludes=("vendor/*",))

    # Act
    with scan.restrict(scope="keep").activate():
        inside = _list_relative(tmp_path)
    outside = _list_relative(tmp_path)

    # Assert
    assert inside == ["keep/a.py"]
    assert outside == ["keep/a.py", "tests/c.py", "vendor/b.py"]


def test_activate_restores_the_previous_scan_when_the_block_raises() -> None:
    # Arrange
    before = get_current_scan()

    # Act
    with pytest.raises(RuntimeError), Scan(scope="x").activate():
        raise RuntimeError("inside")

    # Assert
    assert get_current_scan() is before


def test_restrict_adds_excludes_and_replaces_scope() -> None:
    # Arrange
    base = Scan(root=Path("/p"), scope="a", excludes=("x/*",))

    # Act
    restricted = base.restrict(scope="a/b/", excludes=("y",))
    kept = base.restrict()

    # Assert
    assert (restricted.scope, restricted.excludes) == ("a/b", ("x/*", "y"))
    assert kept == base


def _parse_keep(root: Path) -> object:
    """The tree every check of the current scan is handed for ``keep/a.py``."""
    return next(m.tree for m in sources.iter_modules(root) if m.relative == "keep/a.py")


def test_passes_of_one_run_hand_checks_one_tree(tmp_path: Path) -> None:
    # Arrange
    _write_tree(tmp_path)
    base = Scan(root=tmp_path)

    # Act: a scoped pass, then a whole-tree pass of the same run, then a new run.
    with base.restrict(scope="keep").activate():
        first_pass = _parse_keep(tmp_path)
    with base.activate():
        second_pass = _parse_keep(tmp_path)
    with Scan(root=tmp_path).activate():
        next_run = _parse_keep(tmp_path)

    # Assert: one parse per run, shared by its passes; a new run parses afresh.
    assert second_pass is first_pass
    assert next_run is not first_pass


def test_compatibility_setters_change_the_current_scan(tmp_path: Path) -> None:
    # Arrange
    _write_tree(tmp_path)

    # Act
    discovery.set_excludes(["vendor/*"])
    discovery.set_scope("/keep/")
    walked = _list_relative(tmp_path)
    list(sources.iter_modules(tmp_path))
    before_clear = sources.count_cached()
    sources.clear_cache()

    # Assert
    assert walked == ["keep/a.py"]
    assert (discovery.get_active_excludes(), discovery.get_active_scope()) == (
        ("vendor/*",),
        "keep",
    )
    assert (before_clear, sources.count_cached()) == (1, 0)


def test_register_refuses_a_second_check_under_a_taken_name() -> None:
    # Arrange
    registry = Registry()
    first = _Threshold(limit=1)
    registry.register(first)

    # Act / Assert: the same object again is a no-op, a different one an error.
    registry.register(first)
    with pytest.raises(UsageError, match="'threshold' is already registered"):
        registry.register(_Threshold(limit=2))
    assert registry["threshold"] is first


def test_build_configured_copies_and_keeps_constructor_state() -> None:
    # Arrange: a check that cannot be rebuilt with no arguments.
    template = _Threshold(limit=7)
    registry = Registry({"threshold": template})

    # Act
    configured = registry.build_configured({"threshold": {"limit": 3}})
    defaults = registry.build_configured({})

    # Assert
    assert configured["threshold"].limit == 3
    assert defaults["threshold"].limit == 7
    assert template.limit == 7
    assert configured["threshold"] is not template


def test_a_region_config_does_not_leak_into_the_next_region(tmp_path: Path, capsys) -> None:
    # Arrange: a strict region, then a sibling region (run after it) that
    # tunes another key and so inherits the default parameter warning.
    three_params = "def f(a, b, c):\n    return a\n"
    (tmp_path / "lanorme.toml").write_text("", encoding="utf-8")
    regions = {"a_strict": "param_warn = 2", "b_plain": "param_error = 8"}
    for name, setting in regions.items():
        region = tmp_path / name
        region.mkdir()
        (region / "lanorme.toml").write_text(f"[file_limits]\n{setting}\n", encoding="utf-8")
        (region / "mod.py").write_text(three_params, encoding="utf-8")

    # Act
    main(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "ndjson"])
    out = capsys.readouterr().out

    # Assert: only the strict region's file warns at the lowered threshold.
    assert '"a_strict/mod.py"' in out
    assert '"b_plain/mod.py"' not in out
