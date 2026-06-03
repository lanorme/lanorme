"""Unit tests for transport_layers and architecture-profile support in LayerDepsCheck.

Covers issue #18: multiple transport layers as api-equivalent peers.
Covers issue #19: named architecture presets (profile key).
"""

from __future__ import annotations

from lanorme.checks.layer_deps import LayerDepsCheck


def _codes(violations) -> set[str]:
    return {v.rule.split(":", 1)[0] for v in violations}


def test_transport_layers_get_composition_root_exception(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="domain/model.py", body="VALUE = 1\n")
    tmp_py_file(name="infrastructure/db.py", body="class Repo:\n    pass\n")
    tmp_py_file(name="grpc/dependencies/wire.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()
    check.configure(
        settings={
            "layers": ["domain", "application", "infrastructure", "api", "grpc"],
            "allowed": {
                "application": ["domain"],
                "infrastructure": ["domain", "application"],
                "api": ["domain", "application"],
                "grpc": ["domain", "application"],
            },
            "transport_layers": ["api", "grpc"],
            "composition_root": ["api/dependencies/**", "grpc/dependencies/**"],
        }
    )

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "LAYER-005" not in _codes(result.violations)


def test_backward_compat_api_default_when_transport_layers_empty(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="domain/model.py", body="VALUE = 1\n")
    tmp_py_file(name="infrastructure/db.py", body="class Repo:\n    pass\n")
    tmp_py_file(name="api/dependencies/wire.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()
    # transport_layers not configured — should default to {"api"}

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: api composition root is still allowed without explicit transport_layers
    assert "LAYER-005" not in _codes(result.violations)


def test_four_layer_profile_allows_application_to_import_infrastructure(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="domain/model.py", body="VALUE = 1\n")
    tmp_py_file(name="infrastructure/db.py", body="class Repo:\n    pass\n")
    tmp_py_file(name="application/service.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()
    check.configure(settings={"profile": "four-layer"})

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: four-layer profile permits application -> infrastructure imports
    assert result.violations == []


def test_unknown_profile_emits_warning(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="domain/model.py", body="VALUE = 1\n")
    check = LayerDepsCheck()
    check.configure(settings={"profile": "nonexistent-profile"})

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: an advisory warning is emitted; no hard violation is raised
    warning_rules = {w.rule for w in result.warnings}
    assert any("LAYER-CFG-001" in rule for rule in warning_rules)
    assert result.violations == []
