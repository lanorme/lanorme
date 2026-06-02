"""Tests for the top-level ``source_root`` knob on the two layout-aware checks.

``source_root`` decouples the architectural root from the scan target: a single
``lanorme check .`` from the repo root can classify layers that live under a
nested package directory, while files outside that directory are layer-exempt
and reported paths stay anchored at the scan target.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.layer_deps import LayerDepsCheck
from lanorme.checks.port_coverage import PortCoverageCheck


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _codes(result) -> list[str]:
    return [v.rule.split(":", 1)[0] for v in result.violations]


# --- layer_deps ------------------------------------------------------------- #


def test_layer_violation_missed_without_source_root(tmp_path: Path):
    # Arrange: domain importing application, but nested under src/pkg/.
    _write(tmp_path, "src/pkg/domain/thing.py", "from application.svc import X\n")

    # Act: no source_root, so the path classifies as 'src/...', not a layer.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert result.violations == []


def test_layer_violation_caught_with_source_root(tmp_path: Path):
    # Arrange.
    _write(tmp_path, "src/pkg/domain/thing.py", "from application.svc import X\n")
    check = LayerDepsCheck()
    check.configure(settings={"source_root": "src/pkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: classified as domain, and the reported path is scan-target-relative.
    assert "LAYER-001" in _codes(result)
    assert result.violations[0].file == "src/pkg/domain/thing.py"


def test_file_outside_source_root_is_layer_exempt(tmp_path: Path):
    # Arrange: a stray top-level application/ file that WOULD be flagged if the
    # root were the architectural root.
    _write(tmp_path, "application/legacy.py", "from infrastructure.db import X\n")
    _write(tmp_path, "src/pkg/domain/model.py", "VALUE = 1\n")
    check = LayerDepsCheck()
    check.configure(settings={"source_root": "src/pkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: nothing under src/pkg violates, and the stray file is exempt.
    assert result.status == Status.PASS


def test_composition_root_glob_is_source_root_relative(tmp_path: Path):
    # Arrange: two api files importing infrastructure, one is the comp root.
    _write(tmp_path, "src/pkg/api/dependencies.py", "from infrastructure.db import X\n")
    _write(tmp_path, "src/pkg/api/router.py", "from infrastructure.db import X\n")
    check = LayerDepsCheck()
    check.configure(
        settings={"source_root": "src/pkg", "composition_root": ["api/dependencies.py"]}
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: only the non-comp-root file fires LAYER-005, reported full path.
    files = {v.file for v in result.violations}
    assert files == {"src/pkg/api/router.py"}
    assert "LAYER-005" in _codes(result)


# --- port_coverage ---------------------------------------------------------- #


def test_port001_missed_without_source_root(tmp_path: Path):
    # Arrange: an adapter that imports nothing from ports, nested under src/pkg/.
    _write(tmp_path, "src/pkg/application/ports/registry.py", "class Port:\n    ...\n")
    _write(tmp_path, "src/pkg/infrastructure/services/impl.py", "VALUE = 1\n")

    # Act: defaults look for application/ports + infrastructure/services at root.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS


def test_port001_caught_with_source_root(tmp_path: Path):
    # Arrange.
    _write(tmp_path, "src/pkg/application/ports/registry.py", "class Port:\n    ...\n")
    _write(tmp_path, "src/pkg/infrastructure/services/impl.py", "VALUE = 1\n")
    check = PortCoverageCheck()
    check.configure(settings={"source_root": "src/pkg"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: the adapter is flagged, reported at its scan-target-relative path.
    codes = _codes(result)
    assert "PORT-001" in codes
    flagged = {v.file for v in result.violations if v.rule.startswith("PORT-001")}
    assert flagged == {"src/pkg/infrastructure/services/impl.py"}
