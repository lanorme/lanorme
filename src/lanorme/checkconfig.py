"""Apply a discovered config to the registered checks.

The CLI resolves configuration (discovery, ``extends``, per-directory regions)
and this module hands the result to the checks: each ``[tool.lanorme.<check>]``
sub-table goes to that check's ``configure()``. Both the single-config run and
the cascading runner come through here, so a value the user got wrong is
reported the same way wherever it was written.

The ``*_setting`` readers are for ``configure()`` bodies: each returns the
typed value or raises ``TypeError`` naming the key, which the plumbing below
turns into the usual exit-2 usage error. A check that reads its table through
them never carries a mistyped value into ``run()``.
"""

from __future__ import annotations

from lanorme import ConfigurableCheck, get_all_checks
from lanorme.errors import UsageError

Settings = dict[str, object]


def _reject(*, key: str, expected: str, value: object) -> TypeError:
    return TypeError(f"'{key}' must be {expected}, got {type(value).__name__}")


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


# Top-level ``source_root`` is injected into these layout-aware checks only;
# every other check scans the full target tree.
_SOURCE_ROOT_CHECKS = frozenset({"layer_deps", "port_coverage", "security_patterns"})


def _find_offending_key(*, check: ConfigurableCheck, settings: dict[str, object]) -> str | None:
    """The first key in *settings* the check rejects, when it can be isolated.

    Each key is replayed against a throwaway instance so a partly-configured
    check never reaches the run. A check that cannot be reconstructed with no
    arguments gives no answer, and the caller reports the table alone.
    """
    for key, value in settings.items():
        try:
            probe = type(check)()
        except Exception:  # noqa: BLE001 - an exotic check is not worth probing
            return None
        try:
            probe.configure(settings={key: value})
        except (TypeError, ValueError, AttributeError, KeyError):
            return key
    return None


def _reject_unknown_keys(
    *,
    check: ConfigurableCheck,
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
    raise UsageError(
        f"unknown key in [tool.lanorme.{name}]: {listed}.\n"
        f"  Keys this check reads: {', '.join(sorted(declared))}.",
    )


def _configure_or_fail(*, check: ConfigurableCheck, name: str, settings: dict[str, object]) -> None:
    """Configure one check, turning a rejected value into a usage error.

    Settings arrive from a TOML table the user wrote by hand, so a value of the
    wrong type is a configuration mistake rather than a bug. Report it the way
    every other config failure is reported (exit 2) instead of unwinding a
    traceback from inside the check.
    """
    _reject_unknown_keys(check=check, name=name, settings=settings)
    try:
        check.configure(settings=settings)
    except (TypeError, ValueError, AttributeError, KeyError) as error:
        key = _find_offending_key(check=check, settings=settings)
        location = f"[tool.lanorme.{name}] {key}" if key else f"[tool.lanorme.{name}]"
        raise UsageError(
            f"invalid value for {location}: {error}\n"
            f"  Run 'lanorme check . --show-config' to see the effective settings "
            f"for every check.",
        ) from error


def apply_check_config(*, config: dict[str, object]) -> None:
    """Pass each ``[tool.lanorme.<check>]`` sub-table to that check's configure().

    The top-level ``source_root`` is merged into the settings of the
    layout-aware checks (``layer_deps`` / ``port_coverage``, and
    ``security_patterns`` for the ``api/`` layer AUTHN-001 scans) so a single
    ``lanorme check .`` from the repo root can locate layers under a nested
    package directory while every other check keeps scanning the whole tree.
    """
    source_root = config.get("source_root")
    for name, check in get_all_checks().items():
        if not isinstance(check, ConfigurableCheck):
            continue
        section = config.get(name)
        settings = dict(section) if isinstance(section, dict) else {}
        if (
            name in _SOURCE_ROOT_CHECKS
            and isinstance(source_root, str)
            and source_root
            and "source_root" not in settings
        ):
            settings["source_root"] = source_root
        if settings:
            _configure_or_fail(check=check, name=name, settings=settings)
