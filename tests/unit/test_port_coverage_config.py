"""Unit tests for port_coverage configuration: glob composition root and adapter roots."""

from __future__ import annotations

from lanorme.checks.port_coverage import PortCoverageCheck


def _codes(violations) -> set[str]:
    return {v.rule.split(":", 1)[0] for v in violations}


def _ports_and_adapter(write) -> None:
    write(
        name="application/ports/registry.py",
        body="from typing import Protocol\n\n\nclass Registry(Protocol):\n    def get(self) -> int: ...\n",
    )
    write(
        name="infrastructure/services/registry_impl.py",
        body="from application.ports.registry import Registry\n\n\nclass RegistryImpl:\n    def get(self) -> int:\n        return 1\n",
    )


def test_port003_module_file_comp_root_missed_by_default_but_caught_when_configured(tmp_path, tmp_py_file):
    # Arrange
    _ports_and_adapter(tmp_py_file)
    # An api module-file composition root that imports + instantiates the adapter.
    tmp_py_file(
        name="api/dependencies.py",
        body="from infrastructure.services.registry_impl import RegistryImpl\n\nregistry = RegistryImpl()\n",
    )

    # Act
    default_result = PortCoverageCheck().run(src_root=str(tmp_path))

    configured = PortCoverageCheck()
    configured.configure(settings={"composition_root": ["api/dependencies.py", "api/app.py"]})
    configured_result = configured.run(src_root=str(tmp_path))

    # Assert: default substring globs miss the module file; the config exempts it.
    assert "PORT-003" in _codes(default_result.violations)
    assert "PORT-003" not in _codes(configured_result.violations)


def test_default_directory_comp_root_still_exempt(tmp_path, tmp_py_file):
    # Arrange
    _ports_and_adapter(tmp_py_file)
    tmp_py_file(
        name="api/v1/dependencies/wire.py",
        body="from infrastructure.services.registry_impl import RegistryImpl\n\nregistry = RegistryImpl()\n",
    )

    # Act
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert
    assert "PORT-003" not in _codes(result.violations)


def test_adapter_roots_widened_to_whole_infrastructure(tmp_path, tmp_py_file):
    # Arrange: an adapter living in a per-integration subdir, not under services/.
    tmp_py_file(
        name="application/ports/clock.py",
        body="from typing import Protocol\n\n\nclass Clock(Protocol):\n    def now(self) -> int: ...\n",
    )
    tmp_py_file(
        name="infrastructure/signing/clock_impl.py",
        body="from application.ports.clock import Clock\n\n\nclass ClockImpl:\n    def now(self) -> int:\n        return 0\n",
    )
    check = PortCoverageCheck()
    check.configure(settings={"adapter_roots": ["infrastructure"]})

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: the Clock port is implemented under infrastructure/signing/, so
    # PORT-002 (port has no implementation) must NOT fire once adapter_roots widens.
    assert "PORT-002" not in _codes(result.violations)


def test_adapter_without_ports_import_is_port_001(tmp_path, tmp_py_file):
    # Arrange: the preserved PORT-001 rule, not exercised by the 002/003 tests.
    tmp_py_file(
        name="application/ports/clock.py",
        body="from typing import Protocol\n\n\nclass Clock(Protocol):\n    def now(self) -> int: ...\n",
    )
    # An adapter file that does NOT import any port.
    tmp_py_file(
        name="infrastructure/services/rogue.py",
        body="class Rogue:\n    def now(self) -> int:\n        return 0\n",
    )

    # Act
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert
    assert "PORT-001" in _codes(result.violations)


def test_default_adapter_roots_miss_non_services_subdir(tmp_path, tmp_py_file):
    # Arrange: same adapter under signing/, but default adapter_roots is services/ only.
    tmp_py_file(
        name="application/ports/clock.py",
        body="from typing import Protocol\n\n\nclass Clock(Protocol):\n    def now(self) -> int: ...\n",
    )
    tmp_py_file(
        name="infrastructure/signing/clock_impl.py",
        body="from application.ports.clock import Clock\n\n\nclass ClockImpl:\n    def now(self) -> int:\n        return 0\n",
    )

    # Act
    result = PortCoverageCheck().run(src_root=str(tmp_path))

    # Assert: default only scans infrastructure/services/, so the port looks orphaned.
    assert "PORT-002" in _codes(result.violations)
