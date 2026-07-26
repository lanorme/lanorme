#!/usr/bin/env python3
"""Migrate a legacy service config JSON document to schema v3.

Three schema versions are understood:

v1 (legacy, flat, no ``schema_version`` key)::

    {
      "name": "billing",
      "port": 8080,
      "db": "postgres://user:pw@host:5432/billing",
      "workers": 4,
      "features": "invoices,refunds,dunning",
      "log": "debug",
      "timeout": 30
    }

v2 (``"schema_version": 2``; ``service``/``database`` already split out,
but ``features`` is still a list and ``timeout``/``workers``/``log`` are
still top-level, in seconds)::

    {
      "schema_version": 2,
      "service": {"name": "billing", "listen": {"host": "0.0.0.0", "port": 8080}},
      "database": {"driver": "postgres", "host": "host", "port": 5432,
                   "name": "billing", "credentials": {"user": "user", "password": "pw"}},
      "workers": 4,
      "features": ["invoices", "refunds", "dunning"],
      "log": "debug",
      "timeout": 30
    }

v3 (target shape)::

    {
      "service": {"name": "billing", "listen": {"host": "0.0.0.0", "port": 8080}},
      "database": {"driver": "postgres", "host": "host", "port": 5432,
                   "name": "billing", "credentials": {"user": "user", "password": "pw"}},
      "runtime": {"workers": 4, "timeouts": {"request_ms": 30000}},
      "features": {"invoices": true, "refunds": true, "dunning": true},
      "observability": {"log_level": "DEBUG"}
    }

Usage::

    python config_migrate.py config.v1.json                # print migrated v3 JSON
    python config_migrate.py config.v1.json -o config.v3.json
    python config_migrate.py config.v1.json --dry-run       # print a diff instead
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import sys
from typing import Any
from urllib.parse import urlsplit

ALLOWED_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}

V1_REQUIRED_KEYS = {"name", "port", "db", "workers", "features", "log", "timeout"}
V2_REQUIRED_KEYS = {
    "schema_version",
    "service",
    "database",
    "workers",
    "features",
    "log",
    "timeout",
}
V3_REQUIRED_KEYS = {"service", "database", "runtime", "features", "observability"}
V3_OPTIONAL_KEYS = {"schema_version"}


class ConfigMigrationError(ValueError):
    """Raised when a config document fails validation or cannot be migrated."""


# --------------------------------------------------------------------------
# small validation helpers, shared across schema versions
# --------------------------------------------------------------------------


def _require_keys(
    obj: dict[str, Any], required: set[str], known: set[str], label: str
) -> None:
    if not isinstance(obj, dict):
        raise ConfigMigrationError(f"{label}: expected an object, got {obj!r}")
    missing = sorted(required - obj.keys())
    if missing:
        raise ConfigMigrationError(
            f"{label}: missing required key(s): {', '.join(missing)}"
        )
    unknown = sorted(obj.keys() - known)
    if unknown:
        raise ConfigMigrationError(f"{label}: unknown key(s): {', '.join(unknown)}")


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_port(port: Any, label: str) -> None:
    if not _is_plain_int(port):
        raise ConfigMigrationError(f"{label}: port must be an integer, got {port!r}")
    if not (1 <= port <= 65535):
        raise ConfigMigrationError(
            f"{label}: port {port} is out of range (must be 1-65535)"
        )


def _validate_log_level(log_level: Any, label: str) -> None:
    if not isinstance(log_level, str) or log_level.lower() not in ALLOWED_LOG_LEVELS:
        allowed = ", ".join(sorted(ALLOWED_LOG_LEVELS))
        raise ConfigMigrationError(
            f"{label}: unknown log level {log_level!r} (expected one of: {allowed})"
        )


def _validate_workers(workers: Any, label: str) -> None:
    if not _is_plain_int(workers) or workers < 1:
        raise ConfigMigrationError(
            f"{label}: workers must be a positive integer, got {workers!r}"
        )


def _validate_positive_number(value: Any, label: str, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigMigrationError(
            f"{label}: {what} must be a positive number, got {value!r}"
        )


def _parse_feature_names(names: list[str], label: str) -> dict[str, bool]:
    features: dict[str, bool] = {}
    for raw in names:
        if not isinstance(raw, str) or not raw:
            raise ConfigMigrationError(f"{label}: features contains an empty entry")
        if raw in features:
            raise ConfigMigrationError(f"{label}: duplicate feature {raw!r}")
        features[raw] = True
    if not features:
        raise ConfigMigrationError(f"{label}: features must list at least one feature")
    return features


def _parse_db_url(url: Any, label: str) -> dict[str, Any]:
    if not isinstance(url, str) or not url:
        raise ConfigMigrationError(f"{label}: db must be a non-empty string")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigMigrationError(
            f"{label}: unparseable database URL {url!r}: {exc}"
        ) from exc

    driver = parsed.scheme
    host = parsed.hostname
    name = parsed.path.lstrip("/") if parsed.path else ""
    if not driver or not host or not name:
        raise ConfigMigrationError(f"{label}: unparseable database URL {url!r}")
    if port is None:
        raise ConfigMigrationError(
            f"{label}: unparseable database URL {url!r}: missing port"
        )

    return {
        "driver": driver,
        "host": host,
        "port": port,
        "name": name,
        "credentials": {"user": parsed.username, "password": parsed.password},
    }


def _validate_service(service: Any, label: str) -> None:
    _require_keys(service, {"name", "listen"}, {"name", "listen"}, f"{label}.service")
    name = service["name"]
    if not isinstance(name, str) or not name:
        raise ConfigMigrationError(f"{label}.service: name must be a non-empty string")
    listen = service["listen"]
    _require_keys(listen, {"host", "port"}, {"host", "port"}, f"{label}.service.listen")
    if not isinstance(listen["host"], str) or not listen["host"]:
        raise ConfigMigrationError(
            f"{label}.service.listen: host must be a non-empty string"
        )
    _validate_port(listen["port"], f"{label}.service.listen")


def _validate_database(database: Any, label: str) -> None:
    required = {"driver", "host", "port", "name", "credentials"}
    _require_keys(database, required, required, f"{label}.database")
    if not isinstance(database["driver"], str) or not database["driver"]:
        raise ConfigMigrationError(
            f"{label}.database: driver must be a non-empty string"
        )
    if not isinstance(database["host"], str) or not database["host"]:
        raise ConfigMigrationError(f"{label}.database: host must be a non-empty string")
    _validate_port(database["port"], f"{label}.database")
    if not isinstance(database["name"], str) or not database["name"]:
        raise ConfigMigrationError(f"{label}.database: name must be a non-empty string")
    credentials = database["credentials"]
    _require_keys(
        credentials, {"user", "password"}, {"user", "password"}, f"{label}.database.credentials"
    )


# --------------------------------------------------------------------------
# version detection
# --------------------------------------------------------------------------


def detect_version(config: Any) -> int:
    """Return 1, 2, or 3 for the schema version of ``config``."""
    if not isinstance(config, dict):
        raise ConfigMigrationError(f"config must be a JSON object, got {config!r}")

    schema_version = config.get("schema_version")
    if schema_version is not None:
        if schema_version not in (2, 3):
            raise ConfigMigrationError(
                f"unsupported schema_version: {schema_version!r} (expected 2 or 3)"
            )
        return int(schema_version)

    # No explicit schema_version: v3 documents are structurally distinct
    # from v1 (nested service/database/runtime/observability vs. flat keys).
    if V3_REQUIRED_KEYS <= config.keys():
        return 3

    return 1


# --------------------------------------------------------------------------
# per-version migration / validation
# --------------------------------------------------------------------------


def migrate_v1_to_v3(config: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v1 (flat, legacy) config document to v3."""
    label = "v1 config"
    _require_keys(config, V1_REQUIRED_KEYS, V1_REQUIRED_KEYS, label)

    name = config["name"]
    if not isinstance(name, str) or not name:
        raise ConfigMigrationError(f"{label}: name must be a non-empty string")

    _validate_port(config["port"], label)
    database = _parse_db_url(config["db"], label)

    _validate_workers(config["workers"], label)
    _validate_positive_number(config["timeout"], label, "timeout")

    features_raw = config["features"]
    if not isinstance(features_raw, str):
        raise ConfigMigrationError(
            f"{label}: features must be a comma-separated string, got {features_raw!r}"
        )
    names = [part.strip() for part in features_raw.split(",")]
    features = _parse_feature_names(names, label)

    log_level = config["log"]
    _validate_log_level(log_level, label)

    return {
        "service": {
            "name": name,
            "listen": {"host": "0.0.0.0", "port": config["port"]},
        },
        "database": database,
        "runtime": {
            "workers": config["workers"],
            "timeouts": {"request_ms": int(round(config["timeout"] * 1000))},
        },
        "features": features,
        "observability": {"log_level": log_level.upper()},
    }


def migrate_v2_to_v3(config: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v2 config document (split service/database, flat runtime) to v3."""
    label = "v2 config"
    _require_keys(config, V2_REQUIRED_KEYS, V2_REQUIRED_KEYS, label)

    _validate_service(config["service"], label)
    _validate_database(config["database"], label)

    _validate_workers(config["workers"], label)
    _validate_positive_number(config["timeout"], label, "timeout")

    features_raw = config["features"]
    if not isinstance(features_raw, list):
        raise ConfigMigrationError(
            f"{label}: features must be a list of strings, got {features_raw!r}"
        )
    features = _parse_feature_names(features_raw, label)

    log_level = config["log"]
    _validate_log_level(log_level, label)

    return {
        "service": copy.deepcopy(config["service"]),
        "database": copy.deepcopy(config["database"]),
        "runtime": {
            "workers": config["workers"],
            "timeouts": {"request_ms": int(round(config["timeout"] * 1000))},
        },
        "features": features,
        "observability": {"log_level": log_level.upper()},
    }


def validate_v3(config: dict[str, Any]) -> dict[str, Any]:
    """Validate a v3 config document in place and return it unchanged."""
    label = "v3 config"
    known = V3_REQUIRED_KEYS | V3_OPTIONAL_KEYS
    _require_keys(config, V3_REQUIRED_KEYS, known, label)

    if "schema_version" in config and config["schema_version"] != 3:
        raise ConfigMigrationError(
            f"{label}: schema_version must be 3, got {config['schema_version']!r}"
        )

    _validate_service(config["service"], label)
    _validate_database(config["database"], label)

    runtime = config["runtime"]
    _require_keys(runtime, {"workers", "timeouts"}, {"workers", "timeouts"}, f"{label}.runtime")
    _validate_workers(runtime["workers"], f"{label}.runtime")
    timeouts = runtime["timeouts"]
    _require_keys(timeouts, {"request_ms"}, {"request_ms"}, f"{label}.runtime.timeouts")
    _validate_positive_number(
        timeouts["request_ms"], f"{label}.runtime.timeouts", "request_ms"
    )

    features = config["features"]
    if not isinstance(features, dict) or not features:
        raise ConfigMigrationError(
            f"{label}: features must be a non-empty object of booleans"
        )
    for key, value in features.items():
        if not isinstance(value, bool):
            raise ConfigMigrationError(
                f"{label}.features: {key!r} must be a boolean, got {value!r}"
            )

    observability = config["observability"]
    _require_keys(observability, {"log_level"}, {"log_level"}, f"{label}.observability")
    _validate_log_level(observability["log_level"], f"{label}.observability")

    return config


def migrate(config: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v1, v2, or v3 config document to schema v3.

    Returns a new document; ``config`` is never mutated. Migrating a config
    that is already valid v3 is idempotent: it is validated and returned
    unchanged (aside from being a deep copy).
    """
    version = detect_version(config)
    if version == 1:
        return migrate_v1_to_v3(config)
    if version == 2:
        return migrate_v2_to_v3(config)
    return validate_v3(copy.deepcopy(config))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _format_diff(before: Any, after: Any) -> str:
    before_lines = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    after_lines = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines, after_lines, fromfile="before (as given)", tofile="after (v3)"
    )
    return "".join(diff)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="config_migrate.py",
        description="Migrate a legacy (v1/v2) service config JSON document to schema v3.",
    )
    parser.add_argument("input", help="path to the input config JSON file")
    parser.add_argument(
        "-o",
        "--output",
        help="path to write the migrated v3 config to (default: print to stdout)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do not write any output; print a diff of what would change",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        with open(args.input, encoding="utf-8") as fh:
            try:
                original = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ConfigMigrationError(f"{args.input}: invalid JSON: {exc}") from exc
        migrated = migrate(original)
    except ConfigMigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        diff_text = _format_diff(original, migrated)
        if diff_text:
            sys.stdout.write(diff_text)
        else:
            print("no changes: input is already a valid v3 config")
        return 0

    output_text = json.dumps(migrated, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output_text)
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
