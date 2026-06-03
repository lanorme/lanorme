"""Unit tests for layer_deps configuration, especially the glob composition root.

The headline fix: a composition root that is a module FILE (api/dependencies.py)
is recognised, where the old startswith("api/dependencies/") prefix missed it.
"""

from __future__ import annotations

import tomllib

from lanorme import Status
from lanorme.checks.layer_deps import LayerDepsCheck


def _codes(violations) -> set[str]:
    return {v.rule.split(":", 1)[0] for v in violations}


def _layout(write) -> None:
    # A minimal hexagonal tree: api code importing infrastructure.
    write(name="domain/model.py", body="VALUE = 1\n")
    write(name="infrastructure/db.py", body="class Repo:\n    pass\n")


def _mcp_layout(write) -> None:
    # A hexagonal tree with a non-api transport adapter (mcp_server/) whose
    # composition root (dependencies.py) binds infrastructure.
    _layout(write)
    write(name="mcp_server/dependencies.py", body="from infrastructure.db import Repo\n")


def _core_adapters_check(write) -> LayerDepsCheck:
    # A renamed (core/adapters) layout with a configured check, shared by the
    # tests that probe behaviour on non-default layer names.
    write(name="core/entity.py", body="X = 1\n")
    write(name="adapters/http.py", body="from core.entity import X\n")
    check = LayerDepsCheck()
    check.configure(settings={"layers": ["core", "adapters"], "allowed": {"adapters": ["core"]}})
    return check


# Layout config naming mcp_server/ a classified layer with an explicit allowed
# entry. transport_layers is added per-test so the api-only default can be the
# back-compat control.
_MCP_SETTINGS = {
    "layers": ["domain", "application", "infrastructure", "api", "mcp_server"],
    "composition_root": ["mcp_server/dependencies.py"],
    "allowed": {
        "application": ["domain"],
        "infrastructure": ["domain", "application"],
        "api": ["domain", "application"],
        "mcp_server": ["domain", "application"],
    },
}


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
    check = _core_adapters_check(tmp_py_file)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: adapters -> core is allowed, so no layer violation.
    assert not any(code.startswith("LAYER-00") and code != "LAYER-000" for code in _codes(result.violations))


def test_transport_layer_composition_root_may_import_infra(tmp_path, tmp_py_file):
    # Arrange: mcp_server/ declared a transport peer of api/.
    _mcp_layout(tmp_py_file)
    check = LayerDepsCheck()
    check.configure(settings={**_MCP_SETTINGS, "transport_layers": ["api", "mcp_server"]})

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: the comp-root file may bind infrastructure, exactly as api/ can.
    assert result.violations == []
    assert result.status is Status.PASS


def test_transport_composition_root_still_flagged_under_default(tmp_path, tmp_py_file):
    # Arrange: identical layout and config, but transport_layers keeps the
    # api-only default, so mcp_server is not a transport layer.
    _mcp_layout(tmp_py_file)
    check = LayerDepsCheck()
    check.configure(settings=_MCP_SETTINGS)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: the default does not extend the exception to mcp_server.
    assert "LAYER-005" in _codes(result.violations)


def test_transport_non_comp_root_importing_infra_is_layer_005(tmp_path, tmp_py_file):
    # Arrange: a non-comp-root file in the transport layer importing infra.
    _mcp_layout(tmp_py_file)
    tmp_py_file(name="mcp_server/handlers.py", body="from infrastructure.db import Repo\n")
    check = LayerDepsCheck()
    check.configure(settings={**_MCP_SETTINGS, "transport_layers": ["api", "mcp_server"]})

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: only the composition root is exempt; handlers.py is not.
    assert "LAYER-005" in _codes(result.violations)


def test_unknown_transport_layer_emits_layer_006_warning(tmp_path, tmp_py_file):
    # Arrange: transport_layers names a layer that is absent from layers.
    _layout(tmp_py_file)
    check = LayerDepsCheck()
    check.configure(settings={"transport_layers": ["api", "grpc_server"]})

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: a no-op transport layer is surfaced as an advisory warning.
    assert "LAYER-006" in _codes(result.warnings)


def test_default_transport_layers_does_not_warn_on_renamed_layout(tmp_path, tmp_py_file):
    # Arrange: a core/adapters layout that never opts into transport_layers.
    check = _core_adapters_check(tmp_py_file)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: the default api transport being absent is not the user's concern.
    assert "LAYER-006" not in _codes(result.warnings)


def test_transport_layers_reaches_configure():
    # Arrange: the nested-table shape tomllib delivers.
    pyproject = """
[tool.lanorme.layer_deps]
layers = ["domain", "application", "infrastructure", "api", "mcp_server"]
transport_layers = ["api", "mcp_server"]
"""
    settings = tomllib.loads(pyproject)["tool"]["lanorme"]["layer_deps"]
    check = LayerDepsCheck()

    # Act
    check.configure(settings=settings)

    # Assert
    assert check.transport_layers == ("api", "mcp_server")


def test_empty_transport_layers_keeps_default():
    # Arrange: an empty list must not silently wipe the api exception.
    check = LayerDepsCheck()

    # Act
    check.configure(settings={"transport_layers": []})

    # Assert
    assert check.transport_layers == ("api",)
