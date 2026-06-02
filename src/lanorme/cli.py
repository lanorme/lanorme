"""Command-line entry point for LaNorme.

    lanorme check [PATHS...] [--check NAME] [--select ...] [--ignore ...]
                  [--exclude ...] [--output-format {full,json}] [--json]
                  [--plugin MODULE]
    lanorme rules
    lanorme rule CODE
    lanorme --version

Configuration is discovered by walking up from the target path: a dedicated
``lanorme.toml`` takes precedence, otherwise a ``[tool.lanorme]`` table in
``pyproject.toml``. CLI flags override config.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib
import json
import pkgutil
import re
import sys
import tomllib
from importlib.metadata import entry_points
from importlib.resources import files as _resource_files
from pathlib import Path

import lanorme.checks
from lanorme import (
    CheckResult,
    Configurable,
    Status,
    Violation,
    __version__,
    get_all_checks,
    get_check,
    run_all,
)
from lanorme.discovery import set_excludes

_CODE_RE = re.compile(r"^([A-Z]+)-\d+")

# Top-level ``source_root`` is injected into these two layout-aware checks only;
# every other check scans the full target tree.
_SOURCE_ROOT_CHECKS = frozenset({"layer_deps", "port_coverage"})


# --------------------------------------------------------------------------- #
# Check discovery
# --------------------------------------------------------------------------- #


def _load_builtin_checks() -> None:
    """Import every module under ``lanorme.checks`` so they self-register."""
    for mod in pkgutil.iter_modules(lanorme.checks.__path__, prefix="lanorme.checks."):
        importlib.import_module(mod.name)


def _load_entry_point_checks() -> None:
    """Import plugin modules advertised under the ``lanorme.checks`` group."""
    for ep in entry_points(group="lanorme.checks"):
        importlib.import_module(ep.value.split(":")[0])


def _load_plugin_modules(modules: list[str]) -> None:
    """Import explicitly-named plugin modules so they self-register."""
    for module in modules:
        importlib.import_module(module)


def _apply_check_config(*, config: dict[str, object]) -> None:
    """Pass each ``[tool.lanorme.<check>]`` sub-table to that check's configure().

    The top-level ``source_root`` is merged into the settings of the two
    layout-aware checks (``layer_deps`` / ``port_coverage``) so a single
    ``lanorme check .`` from the repo root can classify layers under a nested
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
            check.configure(settings=settings)


# --------------------------------------------------------------------------- #
# Configuration discovery
# --------------------------------------------------------------------------- #


def _discover_config(*, start: Path) -> tuple[dict, Path]:
    """Walk up from *start* looking for lanorme config. Return (config, project_root)."""
    start = start.resolve()
    search_dir = start if start.is_dir() else start.parent
    for directory in (search_dir, *search_dir.parents):
        dedicated = directory / "lanorme.toml"
        if dedicated.is_file():
            with dedicated.open("rb") as fh:
                return tomllib.load(fh), directory

        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            with pyproject.open("rb") as fh:
                data = tomllib.load(fh)
            tool_cfg = data.get("tool", {}).get("lanorme")
            if tool_cfg is not None:
                return tool_cfg, directory

    return {}, search_dir


# --------------------------------------------------------------------------- #
# Rule-code filtering (select / ignore)
# --------------------------------------------------------------------------- #


def _rule_code(rule: str) -> str:
    """Extract the rule code (e.g. 'LAYER-002') from a rule string."""
    return rule.split(":", 1)[0].strip().split()[0]


def _category(code: str) -> str:
    """The category prefix of a code, e.g. 'LAYER' from 'LAYER-002'."""
    match = _CODE_RE.match(code)
    return match.group(1) if match else code


def _matches(*, code: str, patterns: list[str]) -> bool:
    """True if *code* matches any selector (exact code, category, or 'ALL')."""
    category = _category(code)
    return any(p == "ALL" or p == code or p == category for p in patterns)


def _keep(*, rule: str, select: list[str], ignore: list[str]) -> bool:
    code = _rule_code(rule)
    selected = not select or _matches(code=code, patterns=select)
    return selected and not _matches(code=code, patterns=ignore)


def _apply_filters(
    *,
    results: list[CheckResult],
    select: list[str],
    ignore: list[str],
) -> list[CheckResult]:
    """Drop violations/warnings whose rule code is deselected, recompute status."""
    if not select and not ignore:
        return results

    filtered: list[CheckResult] = []
    for result in results:
        violations = [v for v in result.violations if _keep(rule=v.rule, select=select, ignore=ignore)]
        warnings = [w for w in result.warnings if _keep(rule=w.rule, select=select, ignore=ignore)]
        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        filtered.append(
            CheckResult(
                check=result.check,
                status=status,
                violations=violations,
                warnings=warnings,
            )
        )
    return filtered


def _path_excluded(*, path: str, patterns: list[str]) -> bool:
    normalised = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalised, pattern) for pattern in patterns)


def _apply_excludes(*, results: list[CheckResult], exclude: list[str]) -> list[CheckResult]:
    """Drop violations/warnings whose file path matches an exclude glob."""
    if not exclude:
        return results

    filtered: list[CheckResult] = []
    for result in results:
        violations = [v for v in result.violations if not _path_excluded(path=v.file, patterns=exclude)]
        warnings = [w for w in result.warnings if not _path_excluded(path=w.file, patterns=exclude)]
        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        filtered.append(
            CheckResult(
                check=result.check,
                status=status,
                violations=violations,
                warnings=warnings,
            )
        )
    return filtered


def _per_file_silences(
    *, file: str, rule: str, table: dict[str, list[str]]
) -> bool:
    """True if *rule* (full code or category) is silenced for *file* by *table*."""
    code = _rule_code(rule)
    normalised = file.replace("\\", "/")
    for pattern, codes in table.items():
        if not fnmatch.fnmatch(normalised, pattern):
            continue
        if _matches(code=code, patterns=codes):
            return True
    return False


def _apply_per_file_ignores(
    *,
    results: list[CheckResult],
    table: dict[str, list[str]],
) -> list[CheckResult]:
    """Drop violations whose ``(file, rule)`` pair is silenced by the per-file-ignores table."""
    if not table:
        return results

    filtered: list[CheckResult] = []
    for result in results:
        violations = [
            v for v in result.violations
            if not _per_file_silences(file=v.file, rule=v.rule, table=table)
        ]
        warnings = [
            w for w in result.warnings
            if not _per_file_silences(file=w.file, rule=w.rule, table=table)
        ]
        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        filtered.append(
            CheckResult(
                check=result.check,
                status=status,
                violations=violations,
                warnings=warnings,
            )
        )
    return filtered


_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*([A-Za-z0-9_,\-\s]+))?", re.IGNORECASE)


def _noqa_silences(*, line: str, rule: str) -> bool:
    """True if *line* carries a ``# noqa`` comment that covers *rule*."""
    match = _NOQA_RE.search(line)
    if match is None:
        return False
    if match.group(1) is None:
        return True  # bare `# noqa` silences any rule on this line
    codes = [c.strip() for c in match.group(1).split(",") if c.strip()]
    code = _rule_code(rule)
    return _matches(code=code, patterns=codes)


def _line_at(*, project_root: Path, file: str, line: int, cache: dict[str, list[str]]) -> str:
    """Read source line *line* from *file*, caching the file's lines for the run."""
    key = file.replace("\\", "/")
    lines = cache.get(key)
    if lines is None:
        path = project_root / file
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        cache[key] = lines
    if not lines or line <= 0 or line > len(lines):
        return ""
    return lines[line - 1]


def _apply_noqa(*, results: list[CheckResult], project_root: Path) -> list[CheckResult]:
    """Drop violations/warnings whose source line carries a covering ``# noqa`` comment."""
    cache: dict[str, list[str]] = {}

    def should_keep(violation: Violation) -> bool:
        line = _line_at(
            project_root=project_root, file=violation.file, line=violation.line, cache=cache
        )
        return not _noqa_silences(line=line, rule=violation.rule)

    filtered: list[CheckResult] = []
    for result in results:
        violations = [v for v in result.violations if should_keep(v)]
        warnings = [w for w in result.warnings if should_keep(w)]
        status = Status.FAIL if violations else (Status.WARN if warnings else Status.PASS)
        filtered.append(
            CheckResult(
                check=result.check,
                status=status,
                violations=violations,
                warnings=warnings,
            )
        )
    return filtered


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def _print_results(*, results: list[CheckResult], json_output: bool) -> None:
    if json_output:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return
    for result in results:
        print(result.format_human())
        print()


def _print_rules() -> None:
    checks = get_all_checks()
    if not checks:
        print("No checks registered.")
        return
    for check in sorted(checks.values(), key=lambda c: c.name):
        print(f"\n## {check.name} — {check.description}")
        for rule in check.rules:
            print(f"  {rule}")


def _rules_reference_path() -> Path | None:
    """Locate the rule reference Markdown, preferring the package-bundled copy."""
    bundled = _resource_files("lanorme").joinpath("RULES.md")
    if bundled.is_file():
        return Path(str(bundled))
    for candidate in (
        Path(__file__).resolve().parents[2] / "docs" / "RULES.md",
        Path.cwd() / "docs" / "RULES.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def _rule_reference_section(*, code: str) -> str | None:
    """Return the ``docs/RULES.md`` section that documents *code*, or None.

    Matches by looking for a Markdown header whose backtick-quoted token
    starts with the code (e.g. ``### `CMT-001`: ...``) or a category
    header (e.g. ``## Comments: `CMT-*` ...``) where the code's category
    appears in a backtick token. The section is everything up to the next
    same-or-shallower header.
    """
    reference = _rules_reference_path()
    if reference is None:
        return None
    text = reference.read_text(encoding="utf-8")
    code_token = f"`{code}`"
    category = code.split("-", 1)[0]
    category_token = f"`{category}-"
    lines = text.splitlines()
    exact = [i for i, line in enumerate(lines) if line.startswith("#") and code_token in line]
    category_only = [
        i for i, line in enumerate(lines) if line.startswith("#") and category_token in line
    ]
    if not exact and not category_only:
        return None
    start = exact[0] if exact else category_only[0]
    header_level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("#"):
            level = len(lines[index]) - len(lines[index].lstrip("#"))
            if level <= header_level:
                end = index
                break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _print_rule_detail(*, code: str) -> int:
    """Print the reference section for *code* and return an exit code."""
    section = _rule_reference_section(code=code.upper())
    if section is not None:
        print(section)
        return 0
    print(
        f"No reference section found for {code!r}. Run 'lanorme rules' for the list "
        "of emitted codes, or browse docs/RULES.md directly.",
        file=sys.stderr,
    )
    return 2


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _parse_per_file_ignores(*, table: object) -> dict[str, list[str]]:
    """Normalise the ``[tool.lanorme.per-file-ignores]`` TOML table to {glob: [codes]}."""
    if not isinstance(table, dict):
        return {}
    out: dict[str, list[str]] = {}
    for pattern, codes in table.items():
        if not isinstance(pattern, str) or not isinstance(codes, list):
            continue
        normalised = [c for c in codes if isinstance(c, str)]
        if normalised:
            out[pattern] = normalised
    return out


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lanorme",
        description="La norme — architecture & code-quality linter for Python.",
    )
    parser.add_argument("--version", action="version", version=f"lanorme {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="Run checks against one or more paths.")
    check.add_argument("paths", nargs="*", default=["."], help="Path(s) to check (default: .)")
    check.add_argument("--check", dest="single", default=None, help="Run a single check by name.")
    check.add_argument("--select", default=None, help="Comma-separated rule codes/categories to run.")
    check.add_argument("--ignore", default=None, help="Comma-separated rule codes/categories to skip.")
    check.add_argument("--exclude", default=None, help="Comma-separated file-path globs to exclude.")
    check.add_argument("--plugin", action="append", default=[], help="Plugin module to load (repeatable).")
    check.add_argument(
        "--output-format",
        choices=["full", "json"],
        default="full",
        help="Output format (default: full).",
    )
    check.add_argument("--json", action="store_true", help="Alias for --output-format=json.")

    sub.add_parser("rules", help="List all registered rules and exit.")

    rule = sub.add_parser("rule", help="Print the reference section for a single rule code.")
    rule.add_argument("code", help="The rule code to look up (e.g. CMT-001, SQL-001).")

    return parser


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(2)

    # `rules` needs the registry but no target.
    _load_builtin_checks()
    _load_entry_point_checks()

    if args.command == "rules":
        _print_rules()
        return

    if args.command == "rule":
        sys.exit(_print_rule_detail(code=args.code))

    # command == "check"
    target = Path(args.paths[0])
    if not target.exists():
        print(f"ERROR: path '{target}' does not exist.", file=sys.stderr)
        sys.exit(2)

    config, _project_root = _discover_config(start=target)
    _load_plugin_modules([*config.get("plugins", []), *args.plugin])
    _apply_check_config(config=config)

    select = _csv(args.select) or list(config.get("select", []))
    ignore = _csv(args.ignore) or list(config.get("ignore", []))
    exclude = _csv(args.exclude) or list(config.get("exclude", []))
    per_file_ignores = _parse_per_file_ignores(table=config.get("per-file-ignores", {}))
    json_output = args.json or args.output_format == "json"

    # Publish excludes so checks prune them (plus the built-in junk dirs) at
    # walk time, not just in the post-filter below. Always set, even to (), so
    # a previous in-process run's value does not leak.
    set_excludes(exclude)

    src_root = str(target)
    if args.single:
        check = get_check(args.single)
        if check is None:
            available = ", ".join(sorted(get_all_checks())) or "(none)"
            print(f"ERROR: Unknown check '{args.single}'. Available: {available}", file=sys.stderr)
            sys.exit(2)
        results = [check.run(src_root=src_root)]
    else:
        results = run_all(src_root=src_root)

    if not results:
        print("No checks registered.")
        return

    results = _apply_filters(results=results, select=select, ignore=ignore)
    results = _apply_excludes(results=results, exclude=exclude)
    results = _apply_per_file_ignores(results=results, table=per_file_ignores)
    results = _apply_noqa(results=results, project_root=Path(src_root))
    _print_results(results=results, json_output=json_output)

    if any(r.status == Status.FAIL for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
