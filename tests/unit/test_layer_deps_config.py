"""Unit tests for layer_deps configuration, especially the glob composition root.

The headline fix: a composition root that is a module FILE (api/dependencies.py)
is recognised, where the old startswith("api/dependencies/") prefix missed it.
"""

from __future__ import annotations

import tomllib

from lanorme.checks.layer_deps import LayerDepsCheck


def _codes(violations) -> set[str]:
    return {v.rule.split(":", 1)[0] for v in violations}


def _layout(write) -> None:
    # A minimal hexagonal tree: api code importing infrastructure.
    write(name="domain/model.py", body="VALUE = 1\n")
    write(name="infrastructure/db.py", body="class Repo:\n    pass\n")


def test_api_file_importing_infra_outside_comp_root_is_flagged(tmp_path, tmp_py_file):
    # Arrange
    _layout(tmp_py_file)
    tmp_py_file(name="api/routes.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "LAYER-005" in _codes(result.violations)


def test_default_directory_composition_root_is_allowed(tmp_path, tmp_py_file):
    # Arrange
    _layout(tmp_py_file)
    tmp_py_file(name="api/dependencies/wire.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "LAYER-005" not in _codes(result.violations)


def test_module_file_comp_root_missed_by_default_but_caught_when_configured(tmp_path, tmp_py_file):
    # Arrange
    _layout(tmp_py_file)
    tmp_py_file(name="api/dependencies.py", body="from infrastructure.db import Repo\n")

    # Act: default config does NOT treat the module file as a composition root.
    default_result = LayerDepsCheck().run(src_root=str(tmp_path))

    configured = LayerDepsCheck()
    configured.configure(settings={"composition_root": ["api/dependencies.py", "api/app.py"]})
    configured_result = configured.run(src_root=str(tmp_path))

    # Assert: the one-line config fix is exactly what unblocks the module-file root.
    assert "LAYER-005" in _codes(default_result.violations)
    assert "LAYER-005" not in _codes(configured_result.violations)


def test_domain_importing_infra_is_layer_001(tmp_path, tmp_py_file):
    # Arrange: the preserved inner-layer rule, not exercised by the comp-root tests.
    tmp_py_file(name="infrastructure/db.py", body="class Repo:\n    pass\n")
    tmp_py_file(name="domain/model.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "LAYER-001" in _codes(result.violations)


def test_application_importing_infra_is_layer_002(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="infrastructure/db.py", body="class Repo:\n    pass\n")
    tmp_py_file(name="application/service.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "LAYER-002" in _codes(result.violations)


def test_pyproject_table_shape_reaches_configure(tmp_path):
    # Arrange: the exact nested-table shape the user's parity gate will write.
    pyproject = """
[tool.lanorme.layer_deps]
composition_root = ["api/dependencies.py", "api/app.py"]
layers = ["domain", "application", "infrastructure", "api"]

[tool.lanorme.layer_deps.allowed]
application = ["domain"]
infrastructure = ["domain", "application"]
api = ["domain", "application"]
"""
    settings = tomllib.loads(pyproject)["tool"]["lanorme"]["layer_deps"]
    check = LayerDepsCheck()

    # Act
    check.configure(settings=settings)

    # Assert: tomllib delivers exactly what configure() expects.
    assert check.composition_root == ("api/dependencies.py", "api/app.py")
    assert check.allowed_imports["infrastructure"] == {"domain", "application"}


def test_custom_layers_and_allowed(tmp_path, tmp_py_file):
    # Arrange: a project that calls its layers core/ and adapters/.
    tmp_py_file(name="core/entity.py", body="X = 1\n")
    tmp_py_file(name="adapters/http.py", body="from core.entity import X\n")
    check = LayerDepsCheck()
    check.configure(
        settings={
            "layers": ["core", "adapters"],
            "allowed": {"adapters": ["core"]},
        }
    )

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: adapters -> core is allowed, so no layer violation.
    assert not any(code.startswith("LAYER-00") and code != "LAYER-000" for code in _codes(result.violations))
