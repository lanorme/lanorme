"""Tests for PORT-001 through PORT-003 port/adapter coverage (port_coverage check).

These tests pin the CONFIRMED-CORRECT behaviour observed by running the check
against fixtures laid out with the built-in defaults (ports_dir
``application/ports`` and adapter root ``infrastructure/services``). No
``lanorme.toml`` is needed because every fixture uses the default layout, so the
check is driven directly via ``PortCoverageCheck().run(src_root=...)`` — the same
idiom as ``tests/unit/test_strong_types.py``.

Known defects (NOT encoded as passing tests; see the findings list / xfails):
  * PORT-002 false positive on ``from <pkg> import <module>`` adapter imports.
  * PORT-001 false negative on a substring-overlapping non-ports package.
  * PORT-003 false negative on attribute-form instantiation in api/.
  * PORT-003 firing on TYPE_CHECKING-guarded imports (intent question).
  * PORT-002 missing a sibling Protocol in an already-referenced module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.port_coverage import PortCoverageCheck


def _write(path: Path, body: str) -> None:
    """Create parent dirs and write a UTF-8 source file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _codes(result) -> list[str]:
    """Return the rule-code prefixes of every violation (e.g. 'PORT-001')."""
    return [v.rule.split(":", 1)[0] for v in result.violations]


# --- True negative: a fully wired port/adapter/api layout stays silent --------


def test_clean_layout_passes(tmp_path: Path):
    # Arrange: one port Protocol, an adapter that imports it, and an api file
    # that depends only on the port (no infra coupling at all).
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self, key: str) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self, key: str) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "api/routes.py",
        "from application.ports.registry import Registry\n\n"
        "def handler(reg: Registry) -> str:\n    return reg.get('a')\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: a correct hexagonal layout produces no findings.
    assert result.status == Status.PASS
    assert result.violations == []


# --- True positives -----------------------------------------------------------


def test_adapter_without_ports_import_triggers_port001(tmp_path: Path):
    # Arrange: an adapter file under the adapter root that imports nothing from
    # the ports directory (beside a properly-wired adapter so the layout is real).
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "infrastructure/services/orphan_adapter.py",
        "class OrphanAdapter:\n    def get(self) -> str:\n        return 'x'\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: PORT-001 fires, anchored at line 1 of the orphan adapter.
    assert result.status == Status.FAIL
    port001 = [v for v in result.violations if v.rule.startswith("PORT-001")]
    assert len(port001) == 1
    assert port001[0].file == "infrastructure/services/orphan_adapter.py"
    assert port001[0].line == 1


def test_unimplemented_protocol_triggers_port002(tmp_path: Path):
    # Arrange: two port modules; only registry.py is referenced by an adapter,
    # so the lone Protocol in notifier.py has no implementation.
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "application/ports/notifier.py",
        "from typing import Protocol\n\n"
        "class Notifier(Protocol):\n    def notify(self, msg: str) -> None: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: PORT-002 names the unimplemented Notifier in its own module.
    assert result.status == Status.FAIL
    port002 = [v for v in result.violations if v.rule.startswith("PORT-002")]
    assert len(port002) == 1
    assert port002[0].file == "application/ports/notifier.py"
    assert "Notifier" in port002[0].message


def test_direct_instantiation_in_api_triggers_port003(tmp_path: Path):
    # Arrange: an api/ file that imports an adapter class by name and constructs
    # it directly, outside any composition root.
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "api/bad_routes.py",
        "from infrastructure.services.redis_registry import RedisRegistry\n\n"
        "def handler() -> str:\n    r = RedisRegistry()\n    return r.get()\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: PORT-003 fires as an instantiation finding on the construction line.
    assert result.status == Status.FAIL
    port003 = [v for v in result.violations if v.rule.startswith("PORT-003")]
    assert len(port003) == 1
    assert "instantiat" in port003[0].rule.lower()
    assert port003[0].file == "api/bad_routes.py"
    assert port003[0].line == 4


def test_direct_import_only_in_api_triggers_port003_import_variant(tmp_path: Path):
    # Arrange: an api/ file that imports an adapter class and uses it only as a
    # type annotation (no instantiation) — the import-variant of PORT-003.
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "api/routes.py",
        "from infrastructure.services.redis_registry import RedisRegistry\n\n"
        "def handler(r: RedisRegistry) -> str:\n    return r.get()\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: PORT-003 fires as the "Direct import" variant, not instantiation.
    assert result.status == Status.FAIL
    port003 = [v for v in result.violations if v.rule.startswith("PORT-003")]
    assert len(port003) == 1
    assert "import" in port003[0].rule.lower()
    assert port003[0].file == "api/routes.py"


# --- Adversarial edges confirmed to behave correctly --------------------------


def test_composition_root_is_exempt_from_port003(tmp_path: Path):
    # Arrange: the adapter is constructed inside api/dependencies/, which matches
    # the default composition_root glob "*dependencies/*".
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "api/dependencies/wiring.py",
        "from infrastructure.services.redis_registry import RedisRegistry\n\n"
        "def provide() -> RedisRegistry:\n    return RedisRegistry()\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: wiring at the composition root is allowed — no PORT-003.
    assert result.status == Status.PASS
    assert _codes(result) == []


def test_adapter_name_inside_string_literal_does_not_fire(tmp_path: Path):
    # Arrange: an api/ file whose docstring and a string constant mention the
    # adapter class and a fake "RedisRegistry()" call, but the only real import
    # is the port Protocol. Violation-like prose in strings must not fire.
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "api/routes.py",
        '"""Do not do: from infrastructure.services.redis_registry import '
        'RedisRegistry; RedisRegistry()."""\n'
        "from application.ports.registry import Registry\n\n"
        'EXAMPLE = "RedisRegistry()"\n\n'
        "def handler(r: Registry) -> str:\n    return r.get()\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: no false positive from adapter mentions confined to string literals.
    assert result.status == Status.PASS
    assert _codes(result) == []


def test_init_file_in_adapter_root_is_skipped(tmp_path: Path):
    # Arrange: a package __init__.py that re-exports the adapter but imports
    # nothing from ports; it must be exempt from PORT-001 (default skip_files).
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/__init__.py",
        "from .redis_registry import RedisRegistry\n\n__all__ = ['RedisRegistry']\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: the __init__ re-export is not flagged; the layout is clean.
    assert result.status == Status.PASS
    assert _codes(result) == []


# --- Pinned known defects (xfail) --------------------------------------------


def test_module_form_import_should_not_trigger_port002(tmp_path: Path):
    # Arrange: a genuine adapter implementation that imports the port MODULE and
    # subclasses Registry via the module attribute (idiomatic Python).
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports import registry\n\n"
        "class RedisRegistry(registry.Registry):\n    def get(self) -> str:\n        return 'x'\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert (currently fails): the port is implemented, so nothing should fire.
    assert result.status == Status.PASS
    assert _codes(result) == []


def test_attribute_form_instantiation_in_api_should_trigger_port003(tmp_path: Path):
    # Arrange: an api/ file that imports the adapter MODULE and constructs the
    # class via attribute access, outside the composition root.
    _write(
        tmp_path / "application/ports/registry.py",
        "from typing import Protocol\n\n"
        "class Registry(Protocol):\n    def get(self) -> str: ...\n",
    )
    _write(
        tmp_path / "infrastructure/services/redis_registry.py",
        "from application.ports.registry import Registry\n\n"
        "class RedisRegistry:\n    def get(self) -> str:\n        return 'x'\n",
    )
    _write(
        tmp_path / "api/routes.py",
        "from infrastructure.services import redis_registry\n\n"
        "def handler() -> str:\n    r = redis_registry.RedisRegistry()\n    return r.get()\n",
    )

    # Act.
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert (currently fails): direct construction in api/ must raise PORT-003.
    assert result.status == Status.FAIL
    assert any(c == "PORT-003" for c in _codes(result))
