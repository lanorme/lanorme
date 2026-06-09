"""Tests for LAYER-001 through LAYER-006 hexagonal layer dependency rules.

These mirror the test_strong_types idiom: tmp_path fixtures plus direct
instantiation of the check. Config-dependent cases call
``LayerDepsCheck.configure(settings=...)`` with the INNER settings dict rather
than routing through a ``lanorme.toml``/``pyproject.toml`` file, because a
``lanorme.toml`` expects top-level tables (``[layer_deps]``) while
``[tool.lanorme.layer_deps]`` is pyproject-only -- mixing the two silently drops
the config and would make a config test pass vacuously.

Every test below was verified against current behaviour via real runs. The
confirmed prefix-strip false positive (a third-party submodule named like a
layer) is pinned with an xfail asserting the DESIRED (PASS) behaviour, so the
suite documents the defect without codifying it as correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.layer_deps import LayerDepsCheck


def _write(root: Path, files: dict[str, str]) -> None:
    """Materialise a {relative_path: source} mapping under *root*."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
# True positive / true negative
# --------------------------------------------------------------------------- #


def test_api_importing_infrastructure_is_layer005(tmp_path: Path):
    # Arrange: an api/ route reaches straight into infrastructure/ outside any
    # composition root -- the canonical violation.
    _write(
        tmp_path,
        {
            "api/routes.py": "from infrastructure.db import Session\n",
            "infrastructure/db.py": "class Session: ...\n",
            "domain/models.py": "class Entity: ...\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: a single LAYER-005 failure on the offending line.
    assert result.status == Status.FAIL
    layer005 = [v for v in result.violations if v.rule.startswith("LAYER-005")]
    assert len(layer005) == 1
    assert layer005[0].file == "api/routes.py"
    assert layer005[0].line == 1


def test_clean_layering_is_silent(tmp_path: Path):
    # Arrange: every import points inward, so nothing should fire.
    _write(
        tmp_path,
        {
            "api/routes.py": "from domain.models import Entity\nfrom application.services import svc\n",
            "application/services.py": "from domain.models import Entity\n",
            "domain/models.py": "class Entity: ...\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations
    assert not result.warnings


# --------------------------------------------------------------------------- #
# Per-layer rules
# --------------------------------------------------------------------------- #


def test_domain_importing_application_is_layer001(tmp_path: Path):
    # Arrange: domain must be pure; importing application/ breaks LAYER-001.
    _write(
        tmp_path,
        {
            "domain/models.py": "from application.svc import s\n",
            "application/svc.py": "s = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert any(v.rule.startswith("LAYER-001") for v in result.violations)


def test_application_importing_infrastructure_is_layer002(tmp_path: Path):
    # Arrange: application/ may only see domain/; reaching infrastructure/ is
    # LAYER-002.
    _write(
        tmp_path,
        {
            "application/svc.py": "from infrastructure.db import x\n",
            "infrastructure/db.py": "x = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    assert any(v.rule.startswith("LAYER-002") for v in result.violations)


def test_infrastructure_importing_api_is_layer003(tmp_path: Path):
    # Arrange: a comp-root-looking path inside infrastructure/ is NOT a transport
    # layer, so the exemption does not apply and importing api/ is LAYER-003.
    _write(
        tmp_path,
        {
            "infrastructure/dependencies/wiring.py": "from api.routes import r\n",
            "api/routes.py": "r = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: LAYER-003, proving the comp-root glob is honoured only inside a
    # transport layer.
    assert result.status == Status.FAIL
    assert any(v.rule.startswith("LAYER-003") for v in result.violations)


# --------------------------------------------------------------------------- #
# Composition root (default globs)
# --------------------------------------------------------------------------- #


def test_default_composition_root_directory_may_import_infrastructure(tmp_path: Path):
    # Arrange: api/dependencies/** is a default composition-root glob, so wiring
    # there may bind ports to infrastructure adapters.
    _write(
        tmp_path,
        {
            "api/dependencies/wiring.py": "from infrastructure.db import Session\n",
            "infrastructure/db.py": "class Session: ...\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: exempt -> silent.
    assert result.status == Status.PASS
    assert not result.violations


def test_composition_root_init_package_form_is_exempt(tmp_path: Path):
    # Arrange: the composition root expressed as a package __init__.py still
    # matches the api/dependencies/** glob.
    _write(
        tmp_path,
        {
            "api/dependencies/__init__.py": "from infrastructure.db import Session\n",
            "infrastructure/db.py": "class Session: ...\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: the package form is exempt exactly like a regular module.
    assert result.status == Status.PASS
    assert not result.violations


def test_default_api_v1_main_is_a_composition_root(tmp_path: Path):
    # Arrange: api/v1/main.py is a default single-file composition-root glob.
    _write(
        tmp_path,
        {
            "api/v1/main.py": "from infrastructure.db import x\n",
            "infrastructure/db.py": "x = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


def test_nested_api_file_outside_comp_root_still_fails(tmp_path: Path):
    # Arrange: a deeper api/v1/routes.py is NOT a composition root, so reaching
    # infrastructure/ remains LAYER-005.
    _write(
        tmp_path,
        {
            "api/v1/routes.py": "from infrastructure.db import x\n",
            "infrastructure/db.py": "x = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    layer005 = [v for v in result.violations if v.rule.startswith("LAYER-005")]
    assert len(layer005) == 1
    assert layer005[0].file == "api/v1/routes.py"


def test_module_file_composition_root_via_config(tmp_path: Path):
    # Arrange: a composition root expressed as a single MODULE FILE
    # (api/dependencies.py) is honoured when configured -- glob matching makes a
    # file, not only a directory, a composition root.
    _write(
        tmp_path,
        {
            "api/dependencies.py": "from infrastructure.db import Session\n",
            "infrastructure/db.py": "class Session: ...\n",
        },
    )
    check = LayerDepsCheck()
    check.configure(settings={"composition_root": ["api/dependencies.py", "api/app.py"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


def test_composition_root_may_import_multiple_inner_layers(tmp_path: Path):
    # Arrange: the wiring file pulls infrastructure (via exemption) plus the
    # ordinarily-allowed application and domain layers.
    _write(
        tmp_path,
        {
            "api/dependencies/wiring.py": (
                "from infrastructure.db import x\n"
                "from application.svc import s\n"
                "from domain.models import E\n"
            ),
            "infrastructure/db.py": "x = 1\n",
            "application/svc.py": "s = 1\n",
            "domain/models.py": "E = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


# --------------------------------------------------------------------------- #
# Transport-layer composition-root extension (the headline feature)
# --------------------------------------------------------------------------- #


def _transport_check() -> LayerDepsCheck:
    check = LayerDepsCheck()
    check.configure(
        settings={
            "layers": ["domain", "application", "infrastructure", "api", "mcp"],
            "transport_layers": ["api", "mcp"],
            "composition_root": ["api/dependencies/**", "mcp/dependencies/**"],
            "allowed": {
                "application": ["domain"],
                "infrastructure": ["domain", "application"],
                "api": ["domain", "application"],
                "mcp": ["domain", "application"],
            },
        }
    )
    return check


def test_transport_peer_composition_root_is_exempt(tmp_path: Path):
    # Arrange: a non-api transport peer (mcp) hosts its own composition root that
    # may bind infrastructure adapters.
    _write(
        tmp_path,
        {
            "mcp/dependencies/wiring.py": "from infrastructure.db import x\n",
            "infrastructure/db.py": "x = 1\n",
        },
    )

    # Act.
    result = _transport_check().run(src_root=str(tmp_path))

    # Assert: the peer's wiring file is exempt, just like api/dependencies/.
    assert result.status == Status.PASS
    assert not result.violations


def test_transport_peer_outside_comp_root_still_fails_layer005(tmp_path: Path):
    # Arrange: an ordinary file in the mcp transport peer (not its comp root)
    # must obey the same inward rule as api/.
    _write(
        tmp_path,
        {
            "mcp/server.py": "from infrastructure.db import x\n",
            "infrastructure/db.py": "x = 1\n",
        },
    )

    # Act.
    result = _transport_check().run(src_root=str(tmp_path))

    # Assert: a single LAYER-005 on the peer, proving the peer is not silently
    # exempted wholesale.
    assert result.status == Status.FAIL
    layer005 = [v for v in result.violations if v.rule.startswith("LAYER-005")]
    assert len(layer005) == 1
    assert layer005[0].file == "mcp/server.py"


def test_layer006_warns_when_transport_layer_not_in_layers(tmp_path: Path):
    # Arrange: transport_layers names 'grpc', which is absent from layers, so the
    # advisory LAYER-006 must fire (a warning, not a failure).
    _write(tmp_path, {"domain/m.py": "x = 1\n"})
    check = LayerDepsCheck()
    check.configure(settings={"transport_layers": ["api", "grpc"]})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.WARN
    assert not result.violations
    layer006 = [w for w in result.warnings if w.rule.startswith("LAYER-006")]
    assert len(layer006) == 1
    assert "grpc" in layer006[0].message


def test_default_transport_layer_does_not_warn(tmp_path: Path):
    # Arrange: the default transport_layers (('api',)) was not explicitly set, so
    # LAYER-006 must stay silent even though no api/ files exist.
    _write(tmp_path, {"domain/m.py": "x = 1\n"})

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.warnings


# --------------------------------------------------------------------------- #
# LAYER-004 reachability (only under custom allowed)
# --------------------------------------------------------------------------- #


def test_api_importing_application_is_allowed_by_default(tmp_path: Path):
    # Arrange: under the default layout api/ may import application/.
    _write(
        tmp_path,
        {
            "api/routes.py": "from application.svc import s\n",
            "application/svc.py": "s = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: no LAYER-004 surfaces under defaults.
    assert result.status == Status.PASS
    assert not result.violations


def test_layer004_fires_when_application_stripped_from_api_allowed(tmp_path: Path):
    # Arrange: a custom allowed set removes application/ from api/, which makes
    # the bare api/ rule (LAYER-004) reachable.
    _write(
        tmp_path,
        {
            "api/routes.py": "from application.svc import s\n",
            "application/svc.py": "s = 1\n",
        },
    )
    check = LayerDepsCheck()
    check.configure(
        settings={
            "allowed": {
                "api": ["domain"],
                "application": ["domain"],
                "infrastructure": ["domain", "application"],
            }
        }
    )

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.FAIL
    layer004 = [v for v in result.violations if v.rule.startswith("LAYER-004")]
    assert len(layer004) == 1
    assert layer004[0].file == "api/routes.py"


# --------------------------------------------------------------------------- #
# False-positive guards (must stay silent on correct code)
# --------------------------------------------------------------------------- #


def test_stdlib_imports_in_domain_are_silent(tmp_path: Path):
    # Arrange: domain/ may use pure Python + stdlib freely.
    _write(
        tmp_path,
        {
            "domain/m.py": (
                "import os\nimport json\n"
                "from collections import OrderedDict\n"
                "from dataclasses import dataclass\n"
            ),
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


def test_substring_layer_names_do_not_fire(tmp_path: Path):
    # Arrange: package names that merely CONTAIN a layer word are not layers.
    _write(
        tmp_path,
        {
            "domain/m.py": (
                "import apiclient\n"
                "from infrastructure_utils import helper\n"
                "import domainlib\n"
            ),
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: no false positive from substring matches.
    assert result.status == Status.PASS
    assert not result.violations


def test_importing_own_layer_is_allowed(tmp_path: Path):
    # Arrange: an application/ module importing the domain/ package (the allowed
    # target) in both `import` and `from ... import` forms.
    _write(
        tmp_path,
        {
            "application/svc.py": "import domain\nfrom domain import models\n",
            "domain/__init__.py": "models = 1\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert not result.violations


def test_non_layered_project_is_inert(tmp_path: Path):
    # Arrange: a project with no layer directories at all.
    _write(
        tmp_path,
        {
            "utils/helpers.py": "import os\n",
            "main.py": "from utils.helpers import x\n",
        },
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert: the check produces nothing outside a layered layout.
    assert result.status == Status.PASS
    assert not result.violations
    assert not result.warnings


def test_source_root_exempts_files_outside_it(tmp_path: Path):
    # Arrange: layers live under src/myapp; a domain-named helper outside it must
    # be layer-exempt, and the in-tree domain violation must anchor its path at
    # the scan root (not source_root).
    _write(
        tmp_path,
        {
            "scripts/domain_helper.py": "from application.x import y\n",
            "src/myapp/domain/m.py": "from application.svc import s\n",
            "src/myapp/application/svc.py": "s = 1\n",
        },
    )
    check = LayerDepsCheck()
    check.configure(settings={"source_root": "src/myapp"})

    # Act.
    result = check.run(src_root=str(tmp_path))

    # Assert: exactly one violation, on the in-tree domain file, path anchored at
    # the scan root.
    assert result.status == Status.FAIL
    assert len(result.violations) == 1
    assert result.violations[0].file == "src/myapp/domain/m.py"
    assert result.violations[0].rule.startswith("LAYER-001")


@pytest.mark.xfail(
    reason="CONFIRMED BUG: prefix-strip flags third-party submodules named like a "
    "layer. `from thirdparty.infrastructure import x` in application/ is correct "
    "code but currently fires LAYER-002. This xfail asserts the DESIRED (PASS) "
    "behaviour so the defect is documented, not codified as correct.",
    strict=True,
)
def test_thirdparty_submodule_named_like_layer_is_false_positive(tmp_path: Path):
    # Arrange: a genuine third-party import whose second path segment collides
    # with a layer word.
    _write(
        tmp_path,
        {"application/svc.py": "from thirdparty.infrastructure import client\n"},
    )

    # Act.
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert (DESIRED): correct third-party code must not fire. Currently FAILS
    # with LAYER-002, so this test xfails until the heuristic is fixed.
    assert result.status == Status.PASS
    assert not result.violations
