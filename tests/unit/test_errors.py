"""Exceptions are narrow: a config mistake is a ConfigError, a check's bug is not.

``configure()`` used to have ``AttributeError`` and ``KeyError`` caught and
reported as the user's invalid value, so a plugin bug read as a config
mistake. The typed readers now raise :class:`SettingError`, only a rejected
value (``TypeError``/``ValueError``) becomes a :class:`ConfigError`, and a
``UsageError`` raised inside a check is never swallowed into a crash notice.
"""

from __future__ import annotations

import ast
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

from lanorme import CheckResult, Registry, get_registry, run_audit, run_check
from lanorme.checkconfig import SettingError, read_int, read_str_list
from lanorme.checks.strong_types import StrongTypesCheck
from lanorme.cli import _load_builtin_checks, main
from lanorme.errors import ConfigError, UsageError
from lanorme.presets import _resolve_extends
from lanorme.regions import read_toml
from lanorme.scan import Scan


@dataclass
class _RaisingCheck:
    """A plugin check whose ``configure()``, ``check()`` and ``audit()`` raise what they are told.

    The error is a class attribute so the throwaway instance the config
    plumbing builds to isolate the offending key raises the same.
    """

    error: ClassVar[type[Exception]] = RuntimeError
    name: str = "raising_plugin"
    description: str = "raises on purpose"
    rules: list[str] = field(default_factory=lambda: ["RAISE-001: raises"])

    def configure(self, *, settings: dict[str, object]) -> None:
        raise self.error("boom")

    def check(self, scan: Scan) -> CheckResult:
        raise self.error("boom")

    def audit(self, *, results: dict[str, CheckResult]) -> CheckResult:
        raise self.error("boom")


@pytest.fixture
def plugin(monkeypatch) -> Iterator[_RaisingCheck]:
    """A :class:`_RaisingCheck` whose error class is reset after the test."""
    monkeypatch.setattr(_RaisingCheck, "error", RuntimeError)
    return _RaisingCheck()


def test_typed_reader_raises_a_setting_error_naming_the_key() -> None:
    # Arrange
    settings: dict[str, object] = {"limit": "lots"}

    # Act
    with pytest.raises(SettingError) as caught:
        read_int(settings=settings, key="limit", default=1)

    # Assert
    assert isinstance(caught.value, TypeError)
    assert caught.value.key == "limit"
    assert "'limit' must be an integer, got str" in str(caught.value)


def test_list_reader_refuses_a_bare_string() -> None:
    # Arrange
    settings: dict[str, object] = {"dirs": "vendor"}

    # Act / Assert
    with pytest.raises(SettingError, match="a list of strings"):
        read_str_list(settings=settings, key="dirs")


@pytest.mark.parametrize("error", [TypeError, ValueError])
def test_rejected_value_is_a_config_error_naming_the_table(plugin, error) -> None:
    # Arrange
    type(plugin).error = error

    # Act
    with pytest.raises(ConfigError) as caught:
        Registry({plugin.name: plugin}).build_configured({"raising_plugin": {"level": 3}})

    # Assert
    assert caught.value.key == "level"
    assert caught.value.source == "[tool.lanorme.raising_plugin]"
    assert "invalid value for [tool.lanorme.raising_plugin]" in str(caught.value)


@pytest.mark.parametrize("error", [AttributeError, KeyError])
def test_a_bug_in_configure_is_not_blamed_on_the_config(plugin, error) -> None:
    # Arrange: a plugin whose configure() has a bug of its own.
    type(plugin).error = error

    # Act / Assert: the bug surfaces as itself, not as the user's invalid value.
    with pytest.raises(error):
        Registry({plugin.name: plugin}).build_configured({"raising_plugin": {"level": 3}})


def test_invalid_builtin_value_carries_key_and_source() -> None:
    # Arrange
    _load_builtin_checks()
    config: dict[str, object] = {"file_limits": {"file_error_lines": "lots"}}

    # Act
    with pytest.raises(ConfigError) as caught:
        get_registry().build_configured(config)

    # Assert
    assert isinstance(caught.value, UsageError)
    assert caught.value.key == "file_error_lines"
    assert caught.value.source == "[tool.lanorme.file_limits]"


def test_unknown_check_key_carries_key_and_source() -> None:
    # Arrange
    _load_builtin_checks()
    config: dict[str, object] = {"file_limits": {"file_eror_lines": 400}}

    # Act
    with pytest.raises(ConfigError) as caught:
        get_registry().build_configured(config)

    # Assert
    assert caught.value.key == "file_eror_lines"
    assert caught.value.source == "[tool.lanorme.file_limits]"


def test_malformed_toml_is_a_config_error_naming_the_file(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "lanorme.toml"
    path.write_text("select = [\n", encoding="utf-8")

    # Act
    with pytest.raises(ConfigError) as caught:
        read_toml(path)

    # Assert
    assert caught.value.source == str(path)
    assert caught.value.key is None


def test_unknown_profile_is_a_config_error_on_extends(tmp_path: Path) -> None:
    # Arrange
    config: dict[str, object] = {"extends": "no-such-profile"}

    # Act
    with pytest.raises(ConfigError) as caught:
        _resolve_extends(config=config, project_root=tmp_path)

    # Assert
    assert caught.value.key == "extends"
    assert caught.value.source == "no-such-profile"


def test_usage_error_inside_a_check_propagates(plugin) -> None:
    # Arrange
    type(plugin).error = UsageError

    # Act / Assert: the user's mistake is not hidden in a RUN-000 notice.
    with pytest.raises(UsageError):
        run_check(plugin, src_root=".")
    with pytest.raises(UsageError):
        run_audit(plugin, results={})


def test_any_other_exception_inside_a_check_is_a_crash_notice(plugin) -> None:
    # Arrange
    type(plugin).error = RuntimeError

    # Act
    result = run_check(plugin, src_root=".")

    # Assert
    assert [w.code for w in result.warnings] == ["RUN-000"]
    assert "RuntimeError: boom" in result.warnings[0].message


def test_annotation_too_deep_to_unparse_is_named_not_crashed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Arrange: the unparser overflows on this annotation.
    (tmp_path / "mod.py").write_text("def f(**kw: dict[str, Any]) -> None: ...\n", encoding="utf-8")

    def overflow(node: ast.AST) -> str:
        raise RecursionError("too deep")

    monkeypatch.setattr(ast, "unparse", overflow)

    # Act
    result = StrongTypesCheck().check(Scan(root=tmp_path))

    # Assert
    messages = [v.message for v in result.violations]
    assert any("'<unparseable>'" in message for message in messages)


def test_other_unparse_failures_are_not_swallowed(tmp_path: Path, monkeypatch) -> None:
    # Arrange: anything but an overflow is a bug and must not be rendered away.
    (tmp_path / "mod.py").write_text("def f(**kw: dict[str, Any]) -> None: ...\n", encoding="utf-8")

    def broken(node: ast.AST) -> str:
        raise ValueError("bug")

    monkeypatch.setattr(ast, "unparse", broken)

    # Act
    result = run_check(StrongTypesCheck(), src_root=str(tmp_path))

    # Assert
    assert [w.code for w in result.warnings] == ["RUN-000"]


@dataclass
class _LockedCheck:
    """A plugin check that holds a lock on the instance, so it cannot be deep-copied."""

    name: str = "locked_plugin"
    description: str = "holds a lock"
    rules: list[str] = field(default_factory=lambda: ["LOCK-001: holds a lock"])
    guard: object = field(default_factory=threading.Lock)

    def check(self, scan: Scan) -> CheckResult:
        return CheckResult.from_findings(check=self.name)


def test_a_check_that_cannot_be_copied_is_a_usage_error_naming_it() -> None:
    # Arrange
    registry = Registry({"locked_plugin": _LockedCheck()})

    # Act
    with pytest.raises(UsageError) as caught:
        registry.build_configured({})

    # Assert
    assert not isinstance(caught.value, ConfigError)
    assert "'locked_plugin'" in str(caught.value)
    assert "_LockedCheck" in str(caught.value)
    assert isinstance(caught.value.__cause__, TypeError)


def test_the_cli_exits_2_for_a_check_that_cannot_be_copied(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # Arrange: the registry holds only the locked plugin.
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _load_builtin_checks()
    monkeypatch.setattr("lanorme._registry", Registry({"locked_plugin": _LockedCheck()}))

    # Act
    with pytest.raises(SystemExit) as exited:
        main(["check", str(tmp_path)])

    # Assert
    assert exited.value.code == 2
    assert "ERROR: check 'locked_plugin'" in capsys.readouterr().err
