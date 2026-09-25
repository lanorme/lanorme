"""Apply a discovered config to copies of the registered checks.

The CLI resolves configuration (discovery, ``extends``, per-directory regions)
and this module hands the result to the checks: each ``[tool.lanorme.<check>]``
sub-table goes to the ``configure()`` of a deep copy of that check, so the
registered templates never change. Both the single-config run and the
cascading runner come through here, so a value the user got wrong is reported
the same way wherever it was written.

The typed readers (``read_int``, ``read_str``, ``read_str_list`` and
``is_flag_set``) are for ``configure()`` bodies: each returns the typed value
or raises :class:`SettingError` (a ``TypeError``) naming the key, which the
plumbing below turns into the usual exit-2 :class:`~lanorme.errors.ConfigError`.
A check that reads its table through them never carries a mistyped value into
``run()``. A ``configure()`` that validates a value itself raises
``TypeError`` or ``ValueError``, which is reported the same way; any other
exception is a bug in the check and is left to surface as one.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from lanorme.errors import ConfigError

if TYPE_CHECKING:
    from lanorme import Check

Settings = dict[str, object]


@runtime_checkable
class ConfigurableCheck(Protocol):
    """A check that accepts a ``[tool.lanorme.<name>]`` settings table.

    Import it from ``lanorme``; it is defined here so this module, which the
    package imports, needs nothing from the package itself.
    """

    def configure(self, *, settings: dict[str, object]) -> None:
        """Apply configuration to the check before it runs."""
        ...


# What a ``configure()`` may raise for a value the user wrote wrong. Anything
# else (an ``AttributeError``, a ``KeyError``) is a bug in the check, not in
# the config, and is not dressed up as a usage error.
_REJECTED_VALUE = (TypeError, ValueError)


class SettingError(TypeError):
    """A setting of the wrong type, raised by the typed readers naming the key."""

    def __init__(self, message: str, *, key: str) -> None:
        super().__init__(message)
        self.key = key


def _reject(*, key: str, expected: str, value: object) -> SettingError:
    return SettingError(f"'{key}' must be {expected}, got {type(value).__name__}", key=key)


def is_flag_set(*, settings: Settings, key: str, default: bool) -> bool:
    """A boolean flag; ``true``/``false`` in TOML, nothing else."""
    value = settings.get(key, default)
    if not isinstance(value, bool):
        raise _reject(key=key, expected="true or false", value=value)
    return value


def read_int(*, settings: Settings, key: str, default: int) -> int:
    """An integer threshold; a bool or a float is refused rather than coerced."""
    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject(key=key, expected="an integer", value=value)
    return value


def read_str(*, settings: Settings, key: str, default: str) -> str:
    """A single string, such as a path."""
    value = settings.get(key, default)
    if not isinstance(value, str):
        raise _reject(key=key, expected="a string", value=value)
    return value


def read_str_list(
    *,
    settings: Settings,
    key: str,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """A list of strings; a bare string is refused so it is never iterated by character."""
    value = settings.get(key, default)
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _reject(key=key, expected="a list of strings", value=value)
    return tuple(value)


# The top-level keys the run itself reads. Every other top-level key must be
# the name of a registered check, or it is a mistake.
RUN_KEYS: frozenset[str] = frozenset(
    {
        "select",
        "ignore",
        "exclude",
        "promote",
        "extends",
        "baseline",
        "source_root",
        "plugins",
        "per-file-ignores",
        "root",
    },
)


# Top-level ``source_root`` is injected into these layout-aware checks only;
# every other check scans the full target tree.
_SOURCE_ROOT_CHECKS = frozenset(
    {"layer_deps", "port_coverage", "security_patterns", "test_coverage"},
)


def _find_offending_key(*, template: ConfigurableCheck, settings: Settings) -> str | None:
    """The first key in *settings* the check rejects, when it can be isolated.

    Each key is replayed against its own copy of the unconfigured *template*,
    so a partly-configured check never reaches the run.
    """
    for key, value in settings.items():
        try:
            copy.deepcopy(template).configure(settings={key: value})
        except _REJECTED_VALUE:
            return key
    return None


def _reject_unknown_keys(
    *,
    check: Check,
    name: str,
    settings: dict[str, object],
) -> None:
    """Refuse a table that names a key the check does not declare.

    A check that declares ``settings_keys`` (the TOML keys its ``configure()``
    reads) gets a mistyped key reported like a mistyped value, instead of the
    key being ignored and the default silently kept. A check without the
    declaration accepts anything, as before.
    """
    declared = getattr(check, "settings_keys", None)
    if declared is None:
        return
    unknown = sorted(key for key in settings if key not in declared)
    if not unknown:
        return
    listed = ", ".join(repr(key) for key in unknown)
    raise ConfigError(
        f"unknown key in [tool.lanorme.{name}]: {listed}.\n"
        f"  Keys this check reads: {', '.join(sorted(declared))}.",
        key=unknown[0],
        source=f"[tool.lanorme.{name}]",
    )


def _configure_or_fail(
    *,
    check: ConfigurableCheck,
    template: ConfigurableCheck,
    name: str,
    settings: Settings,
) -> None:
    """Configure one check, turning a rejected value into a config error.

    Settings arrive from a TOML table the user wrote by hand, so a value of the
    wrong type is a configuration mistake rather than a bug. Report it the way
    every other config failure is reported (exit 2) instead of unwinding a
    traceback from inside the check. Only a rejected value is reported so: an
    ``AttributeError`` or a ``KeyError`` out of ``configure()`` is the check's
    own bug and propagates as one.
    """
    _reject_unknown_keys(check=check, name=name, settings=settings)
    try:
        check.configure(settings=settings)
    except _REJECTED_VALUE as error:
        key = _find_offending_key(template=template, settings=settings)
        table = f"[tool.lanorme.{name}]"
        location = f"{table} {key}" if key else table
        raise ConfigError(
            f"invalid value for {location}: {error}\n"
            f"  Run 'lanorme check . --show-config' to see the effective settings "
            f"for every check.",
            key=key,
            source=table,
        ) from error


def _build_settings(*, name: str, config: Settings) -> Settings:
    """The settings table for check *name*, with the top-level ``source_root`` injected.

    The top-level ``source_root`` is merged into the settings of the
    layout-aware checks (``layer_deps`` / ``port_coverage``,
    ``security_patterns`` for the ``api/`` layer AUTHN-001 scans, and
    ``test_coverage`` for its production directories) so a single
    ``lanorme check .`` from the repo root can locate layers under a nested
    package directory while every other check keeps scanning the whole tree.
    """
    section = config.get(name)
    settings = dict(section) if isinstance(section, dict) else {}
    source_root = config.get("source_root")
    if (
        name in _SOURCE_ROOT_CHECKS
        and isinstance(source_root, str)
        and source_root
        and "source_root" not in settings
    ):
        settings["source_root"] = source_root
    return settings


def configure_checks(*, templates: Mapping[str, Check], config: Settings) -> dict[str, Check]:
    """A deep copy of each of *templates*, configured from its ``[tool.lanorme.<name>]`` table.

    The templates are never touched, so the same registered checks serve every
    region of a run and every run in a process. A value a check rejects is a
    :class:`~lanorme.errors.ConfigError` naming the table and, where it can be
    isolated, the key.
    """
    configured: dict[str, Check] = {}
    for name, template in templates.items():
        check = copy.deepcopy(template)
        if isinstance(check, ConfigurableCheck):
            settings = _build_settings(name=name, config=config)
            if settings:
                _configure_or_fail(check=check, template=template, name=name, settings=settings)
        configured[name] = check
    return configured
