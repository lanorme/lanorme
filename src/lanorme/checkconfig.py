"""Apply a discovered config to the registered checks.

The CLI resolves configuration (discovery, ``extends``, per-directory regions)
and this module hands the result to the checks: each ``[tool.lanorme.<check>]``
sub-table goes to that check's ``configure()``. Both the single-config run and
the cascading runner come through here, so a value the user got wrong is
reported the same way wherever it was written.
"""

from __future__ import annotations

import sys

from lanorme import Configurable, get_all_checks

# Top-level ``source_root`` is injected into these layout-aware checks only;
# every other check scans the full target tree.
_SOURCE_ROOT_CHECKS = frozenset({"layer_deps", "port_coverage", "security_patterns"})


def _offending_key(*, check: Configurable, settings: dict[str, object]) -> str | None:
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
        except (TypeError, ValueError):
            return key
    return None


def _configure_or_fail(*, check: Configurable, name: str, settings: dict[str, object]) -> None:
    """Configure one check, turning a rejected value into a usage error.

    Settings arrive from a TOML table the user wrote by hand, so a value of the
    wrong type is a configuration mistake rather than a bug. Report it the way
    every other config failure is reported (exit 2) instead of unwinding a
    traceback from inside the check.
    """
    try:
        check.configure(settings=settings)
    except (TypeError, ValueError) as error:
        key = _offending_key(check=check, settings=settings)
        location = f"[tool.lanorme.{name}] {key}" if key else f"[tool.lanorme.{name}]"
        print(
            f"ERROR: invalid value for {location}: {error}\n"
            f"  Run 'lanorme check . --show-config' to see the effective settings "
            f"for every check.",
            file=sys.stderr,
        )
        sys.exit(2)


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
        if not isinstance(check, Configurable):
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
