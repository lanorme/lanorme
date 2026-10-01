"""Lint, security, duplication and dead-code measurements: ruff, bandit, pylint, vulture."""

import json
import re
from collections import Counter
from pathlib import Path

from measure_size import GROUPS, classify_file
from measure_tools import run_uvx

DUPLICATE_MIN_LINES = (4, 6)
VULTURE_CONFIDENCES = (60, 80)
DUPLICATE_RANGE = re.compile(r"^==[^:]+:\[(\d+):(\d+)\]", re.MULTILINE)
VULTURE_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+): (?P<message>.*) \((?P<conf>\d+)% confidence",
    re.MULTILINE,
)
# Framework entry points vulture cannot see being called: route handlers,
# dependency and validator hooks, and the uvicorn factory named in the contract.
VULTURE_IGNORE_DECORATORS = ",".join(
    [
        "@*.get", "@*.post", "@*.put", "@*.patch", "@*.delete", "@*.api_route",
        "@*.websocket", "@*.on_event", "@*.middleware", "@*.exception_handler",
        "@*.validator", "@field_validator", "@model_validator", "@validator",
        "@tool", "@*.tool", "@*.fixture", "@pytest.fixture",
    ],
)  # fmt: skip
VULTURE_IGNORE_NAMES = "create_app,model_config,lifespan"


def measure_ruff(root: Path, *, sloc: dict[str, int]) -> dict[str, object]:
    """``ruff check --select ALL`` under one shared config, findings by group and category."""
    args = [
        "check", ".", "--select", "ALL", "--isolated", "--target-version", "py313",
        "--output-format", "json", "--exit-zero", "--no-cache",
    ]  # fmt: skip
    run = run_uvx("ruff", args=args, cwd=root)
    try:
        findings = json.loads(run.stdout)
    except json.JSONDecodeError:
        return {"error": run.build_error_tail()}
    linters = _read_ruff_linters(root)
    by_group: dict[str, list[dict]] = {group: [] for group in GROUPS}
    for finding in findings:
        rel = Path(finding["filename"]).resolve().relative_to(root.resolve())
        by_group[classify_file(rel)].append(finding)
    return {
        group: _summarise_ruff(by_group[group], linters=linters, sloc=sloc.get(group, 0))
        for group in GROUPS
    }


def _read_ruff_linters(root: Path) -> dict[str, str]:
    """Rule-code prefix to linter name, from ``ruff linter``, longest prefixes first."""
    run = run_uvx("ruff", args=["linter", "--output-format", "json"], cwd=root)
    prefixes: dict[str, str] = {}
    for linter in json.loads(run.stdout or "[]"):
        for category in linter.get("categories") or []:
            prefixes[category["prefix"]] = linter["name"]
        if linter.get("prefix"):
            prefixes[linter["prefix"]] = linter["name"]
    return dict(sorted(prefixes.items(), key=lambda item: -len(item[0])))


def _summarise_ruff(
    findings: list[dict],
    *,
    linters: dict[str, str],
    sloc: int,
) -> dict[str, object]:
    codes = Counter(finding["code"] or "syntax-error" for finding in findings)
    prefixes = Counter()
    categories = Counter()
    for code, count in codes.items():
        prefix = re.match(r"[A-Z]+", code)
        prefixes[prefix.group() if prefix else code] += count
        categories[_find_linter(code, linters=linters)] += count
    total = sum(codes.values())
    return {
        "total": total,
        "per_100_sloc": round(100 * total / sloc, 2) if sloc else None,
        "by_category": dict(categories.most_common()),
        "by_prefix": dict(prefixes.most_common()),
        "by_code": dict(codes.most_common()),
    }


def _find_linter(code: str, *, linters: dict[str, str]) -> str:
    for prefix, name in linters.items():
        if code.startswith(prefix) and code[len(prefix) :].isdigit():
            return name
    return "unknown"


def measure_bandit(root: Path, *, app_files: list[Path]) -> dict[str, object]:
    """``bandit -f json`` over the app code, findings by severity and confidence."""
    if not app_files:
        return {"total": 0, "by_severity": {}, "by_confidence": {}, "by_test": {}}
    run = run_uvx("bandit", args=["-f", "json", "-q", *map(str, app_files)], cwd=root)
    try:
        report = json.loads(run.stdout)
    except json.JSONDecodeError:
        return {"error": run.build_error_tail()}
    results = report.get("results", [])
    return {
        "total": len(results),
        "by_severity": dict(Counter(item["issue_severity"] for item in results)),
        "by_confidence": dict(Counter(item["issue_confidence"] for item in results)),
        "by_test": dict(Counter(f"{item['test_id']} {item['test_name']}" for item in results)),
        "errors": len(report.get("errors", [])),
    }


def measure_duplication(root: Path, *, app_files: list[Path], scratch: Path) -> dict[str, object]:
    """pylint duplicate-code over the app code at each minimum block size."""
    rcfile = scratch / "empty.pylintrc"
    rcfile.write_text("", encoding="utf-8")
    return {
        f"min_lines_{size}": _run_duplicate_code(root, files=app_files, rcfile=rcfile, size=size)
        for size in DUPLICATE_MIN_LINES
    }


def _run_duplicate_code(
    root: Path,
    *,
    files: list[Path],
    rcfile: Path,
    size: int,
) -> dict[str, object]:
    if len(files) < 2:
        return {"blocks": 0, "duplicated_lines": 0, "lines_in_blocks": 0}
    args = [
        f"--rcfile={rcfile}", "--disable=all", "--enable=duplicate-code",
        f"--min-similarity-lines={size}", "--output-format=json", "--jobs=1",
        *map(str, files),
    ]  # fmt: skip
    run = run_uvx("pylint", args=args, cwd=root)
    try:
        messages = [m for m in json.loads(run.stdout) if m.get("symbol") == "duplicate-code"]
    except json.JSONDecodeError:
        return {"error": run.build_error_tail()}
    redundant = in_blocks = 0
    for message in messages:
        spans = [
            int(end) - int(start) for start, end in DUPLICATE_RANGE.findall(message["message"])
        ]
        if spans:
            in_blocks += sum(spans)
            redundant += max(spans) * (len(spans) - 1)
    return {"blocks": len(messages), "duplicated_lines": redundant, "lines_in_blocks": in_blocks}


def measure_vulture(root: Path, *, files: dict[str, list[Path]]) -> dict[str, object]:
    """vulture over app code alone, and over app plus tests counting only app findings."""
    app = [str(rel) for rel in files["app"]]
    with_tests = app + [str(rel) for rel in files["tests"]]
    app_names = set(app)
    return {
        "app_only": _run_vulture(root, targets=app, keep=app_names),
        "app_with_tests": _run_vulture(root, targets=with_tests, keep=app_names),
    }


def _run_vulture(root: Path, *, targets: list[str], keep: set[str]) -> dict[str, object]:
    if not targets:
        return {f"min_confidence_{c}": 0 for c in VULTURE_CONFIDENCES}
    args = [
        "--min-confidence", str(min(VULTURE_CONFIDENCES)),
        "--ignore-decorators", VULTURE_IGNORE_DECORATORS,
        "--ignore-names", VULTURE_IGNORE_NAMES,
        *targets,
    ]  # fmt: skip
    run = run_uvx("vulture", args=args, cwd=root)
    if run.returncode not in (0, 3):
        return {"error": run.build_error_tail()}
    found = [m for m in VULTURE_LINE.finditer(run.stdout) if m["file"] in keep]
    summary: dict[str, object] = {
        f"min_confidence_{c}": sum(int(m["conf"]) >= c for m in found) for c in VULTURE_CONFIDENCES
    }
    summary["findings"] = [f"{m['file']}:{m['line']} {m['message']} ({m['conf']}%)" for m in found]
    return summary
