"""Tests for IMPORT-001 and ENDPOINT-001 pattern divergence detection.

The regression of note: an endpoint file with a very deep attribute chain
overflowed the recursive ``_max_nesting_depth`` walk and crashed the whole run.
One bad file must be skipped with an ENDPOINT-000 warning, not be fatal, and the
rest of the tree must still be checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.pattern_divergence import PatternDivergenceCheck

_ENDPOINTS_DIR = "api/v1/endpoints"

# A module-level import plus an inline import inside a function body. The inline
# import is the IMPORT-001 violation; the module-level one must not be flagged.
_INLINE_IMPORT_SRC = (
    "import os\n"
    "\n"
    "def handler():\n"
    "    import json\n"
    "    return json.dumps({})\n"
)

# Same shape but with the import hoisted to module level: no IMPORT-001.
_MODULE_IMPORT_SRC = (
    "import os\n"
    "import json\n"
    "\n"
    "def handler():\n"
    "    return json.dumps({})\n"
)


def _nested_endpoint(*, depth: int) -> str:
    """Return an endpoint function with *depth* nested ``if`` blocks."""
    lines = ["def endpoint():"]
    for level in range(depth):
        lines.append("    " * (level + 1) + "if True:")
    lines.append("    " * (depth + 1) + "pass")
    return "\n".join(lines) + "\n"


@pytest.fixture
def endpoints_dir(tmp_path: Path) -> Path:
    """Create and return the api/v1/endpoints directory under a temp root."""
    target = tmp_path / _ENDPOINTS_DIR
    target.mkdir(parents=True)
    return target


def test_deeply_nested_endpoint_is_skipped_not_crashed(
    tmp_path: Path,
    endpoints_dir: Path,
):
    # Arrange: an endpoint file whose attribute chain (4000 deep) overflows the
    # recursive depth walk, beside a genuine IMPORT-001 violation elsewhere.
    chain = "a" + ".b" * 4000
    (endpoints_dir / "deep.py").write_text(
        f"def endpoint():\n    return {chain}\n", encoding="utf-8"
    )
    (tmp_path / "service.py").write_text(_INLINE_IMPORT_SRC, encoding="utf-8")

    # Act: the run must complete rather than raise RecursionError.
    result = PatternDivergenceCheck().run(src_root=str(tmp_path))

    # Assert: the deep file is skipped with an ENDPOINT-000 warning, and the
    # genuine inline-import violation is still detected.
    assert result.status == Status.FAIL
    assert any(w.rule.startswith("ENDPOINT-000") for w in result.warnings)
    assert any(v.rule.startswith("IMPORT-001") for v in result.violations)


def test_inline_import_is_flagged(tmp_path: Path):
    # Arrange: a function body containing an inline import.
    (tmp_path / "service.py").write_text(_INLINE_IMPORT_SRC, encoding="utf-8")

    # Act.
    result = PatternDivergenceCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    import_violations = [v for v in result.violations if v.rule.startswith("IMPORT-001")]
    assert len(import_violations) == 1
    assert "json" in import_violations[0].message


def test_module_level_imports_are_clean(tmp_path: Path):
    # Arrange: the same module with imports hoisted to module level.
    (tmp_path / "service.py").write_text(_MODULE_IMPORT_SRC, encoding="utf-8")

    # Act.
    result = PatternDivergenceCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations
    assert not result.warnings


def test_endpoint_nesting_at_threshold_is_clean(endpoints_dir: Path, tmp_path: Path):
    # Arrange (boundary): nesting depth exactly 4 sits on the threshold.
    (endpoints_dir / "ok.py").write_text(
        _nested_endpoint(depth=4), encoding="utf-8"
    )

    # Act.
    result = PatternDivergenceCheck().run(src_root=str(tmp_path))

    # Assert: no ENDPOINT-001 warning at the boundary.
    assert not any(w.rule.startswith("ENDPOINT-001") for w in result.warnings)


def test_deeply_nested_endpoint_is_flagged(endpoints_dir: Path, tmp_path: Path):
    # Arrange: nesting depth 5 exceeds the threshold of 4.
    (endpoints_dir / "deep_nest.py").write_text(
        _nested_endpoint(depth=5), encoding="utf-8"
    )

    # Act.
    result = PatternDivergenceCheck().run(src_root=str(tmp_path))

    # Assert: an ENDPOINT-001 warning is raised (a warning, not a violation).
    assert result.status == Status.WARN
    endpoint_warnings = [w for w in result.warnings if w.rule.startswith("ENDPOINT-001")]
    assert len(endpoint_warnings) == 1
    assert "endpoint" in endpoint_warnings[0].message
