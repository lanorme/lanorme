"""Unit tests for port_coverage configuration: glob composition root and adapter roots."""

from __future__ import annotations

from lanorme.checks.port_coverage import PortCoverageCheck
from lanorme.scan import Scan


def _collect_codes(violations) -> set[str]:
    return {v.rule.split(":", 1)[0] for v in violations}


def _write_ports_and_adapter(write) -> None:
    write(
        name="application/ports/registry.py",
        body="from typing import Protocol\n\n\nclass Registry(Protocol):\n    def get(self) -> int: ...\n",
    )
    write(
        name="infrastructure/services/registry_impl.py",
        body="from application.ports.registry import Registry\n\n\nclass RegistryImpl:\n    def get(self) -> int:\n        return 1\n",
    )


def test_port003_module_file_comp_root_exempt_by_default_and_another_file_when_configured(
    tmp_path,
    tmp_py_file,
):
    # Arrange: api/dependencies.py (the canonical composition-root FILE) and an
    # app factory, both importing and instantiating the adapter.
    _write_ports_and_adapter(tmp_py_file)
    wiring = "from infrastructure.services.registry_impl import RegistryImpl\n\nregistry = RegistryImpl()\n"
    tmp_py_file(name="api/dependencies.py", body=wiring)
    tmp_py_file(name="api/app.py", body=wiring)

    # Act
    default_result = PortCoverageCheck().check(Scan(root=tmp_path))

    configured = PortCoverageCheck()
    configured.configure(settings={"composition_root": ["api/app.py"]})
    configured_result = configured.check(Scan(root=tmp_path))

    # Assert: the defaults exempt the module file; the factory needs the config,
    # which then names the only composition root.
    assert [v.file for v in default_result.violations] == ["api/app.py"]
    assert _collect_codes(default_result.violations) == {"PORT-003"}
    assert [v.file for v in configured_result.violations] == ["api/dependencies.py"]


def test_default_directory_comp_root_still_exempt(tmp_path, tmp_py_file):
    # Arrange
    _write_ports_and_adapter(tmp_py_file)
    tmp_py_file(
        name="api/v1/dependencies/wire.py",
        body="from infrastructure.services.registry_impl import RegistryImpl\n\nregistry = RegistryImpl()\n",
    )

    # Act
    result = PortCoverageCheck().check(Scan(root=tmp_path))

    # Assert
    assert "PORT-003" not in _collect_codes(result.violations)


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
    result = check.check(Scan(root=tmp_path))

    # Assert: the Clock port is implemented under infrastructure/signing/, so
    # PORT-002 (port has no implementation) must NOT fire once adapter_roots widens.
    assert "PORT-002" not in _collect_codes(result.violations)


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
    result = PortCoverageCheck().check(Scan(root=tmp_path))

    # Assert
    assert "PORT-001" in _collect_codes(result.violations)


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
    result = PortCoverageCheck().check(Scan(root=tmp_path))

    # Assert: default only scans infrastructure/services/, so the port looks orphaned.
    assert "PORT-002" in _collect_codes(result.violations)
