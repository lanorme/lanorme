"""Measurements that need the project's own environment: uv sync, mypy, pytest + coverage.

All of them run in the temporary copy, so the ``.venv`` uv creates never
touches the snapshot.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from measure_tools import PINNED, run_command

SYNC_TIMEOUT = 900
MYPY_TIMEOUT = 600
PYTEST_TIMEOUT = 600
MYPY_ERROR = re.compile(
    r"^[^:\n]+:\d+(?::\d+)?: error: .*?(?:\[(?P<code>[\w-]+)\])?$",
    re.MULTILINE,
)
PYTEST_PLUGINS = ["pytest", "pytest-cov", "httpx"]


def sync_project(root: Path) -> dict[str, object]:
    """``uv sync`` the copy (dev group included); the record says whether it worked."""
    has_lock = (root / "uv.lock").is_file()
    if not (root / "pyproject.toml").is_file():
        return {"ok": False, "has_lock": has_lock, "error": "no pyproject.toml"}
    run = run_command(["uv", "sync"], cwd=root, timeout=SYNC_TIMEOUT)
    record: dict[str, object] = {
        "ok": run.returncode == 0,
        "has_lock": has_lock,
        "seconds": round(run.seconds, 1),
    }
    if run.returncode != 0:
        record["error"] = "timed out" if run.timed_out else run.build_error_tail()
    return record


def measure_mypy(root: Path) -> dict[str, object]:
    """mypy over ``app`` with the project's own mypy config ignored, default and strict."""
    return {mode: _run_mypy(root, strict=(mode == "strict")) for mode in ("default", "strict")}


def _run_mypy(root: Path, *, strict: bool) -> dict[str, object]:
    args = [
        "uv", "run", "--no-sync", "--with", f"mypy=={PINNED['mypy']}",
        "mypy", "app", "--ignore-missing-imports", "--config-file=",
        "--no-error-summary", "--show-error-codes",
    ]  # fmt: skip
    if strict:
        args.append("--strict")
    run = run_command(args, cwd=root, timeout=MYPY_TIMEOUT)
    if run.timed_out or run.returncode not in (0, 1):
        return {"error": "timed out" if run.timed_out else run.build_error_tail()}
    errors = list(MYPY_ERROR.finditer(run.stdout))
    by_code: dict[str, int] = {}
    for error in errors:
        code = error["code"] or "none"
        by_code[code] = by_code.get(code, 0) + 1
    files = {error.group().split(":", 1)[0] for error in errors}
    return {
        "errors": len(errors),
        "files_with_errors": len(files),
        "by_code": dict(sorted(by_code.items(), key=lambda item: -item[1])),
    }


def measure_pytest(root: Path, *, scratch: Path) -> dict[str, object]:
    """The agent's own tests with coverage of ``app``: outcomes and line coverage."""
    junit = scratch / "junit.xml"
    coverage = scratch / "coverage.json"
    args = ["uv", "run", "--no-sync"]
    for plugin in PYTEST_PLUGINS:
        args += ["--with", plugin]
    args += [
        "pytest", "-q", "-p", "no:cacheprovider", "--cov=app",
        f"--cov-report=json:{coverage}", f"--junitxml={junit}",
    ]  # fmt: skip
    run = run_command(args, cwd=root, timeout=PYTEST_TIMEOUT)
    record: dict[str, object] = {
        "exit_code": run.returncode,
        "timed_out": run.timed_out,
        "seconds": round(run.seconds, 1),
        **_read_junit(junit),
        "line_coverage_percent": _read_coverage(coverage),
        "versions": _read_plugin_versions(root),
    }
    if run.timed_out or run.returncode not in (0, 1):
        record["error_tail"] = run.build_error_tail()
    return record


def _read_junit(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"tests": None, "passed": None, "failed": None, "errors": None, "skipped": None}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    passed = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    ran = passed + totals["failures"] + totals["errors"]
    return {
        "tests": totals["tests"],
        "passed": passed,
        "failed": totals["failures"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "pass_rate": round(passed / ran, 4) if ran else None,
    }


def _read_coverage(path: Path) -> float | None:
    if not path.is_file():
        return None
    totals = json.loads(path.read_text(encoding="utf-8")).get("totals", {})
    percent = totals.get("percent_covered")
    return round(percent, 2) if percent is not None else None


def _read_plugin_versions(root: Path) -> dict[str, str]:
    """The pytest, pytest-cov, coverage and httpx versions the test run resolved."""
    names = ["pytest", "pytest-cov", "coverage", "httpx"]
    script = (
        "import importlib.metadata as m, json\n"
        f"print(json.dumps({{n: m.version(n) for n in {names!r}}}))"
    )
    args = ["uv", "run", "--no-sync"]
    for plugin in PYTEST_PLUGINS:
        args += ["--with", plugin]
    run = run_command([*args, "python", "-c", script], cwd=root, timeout=120)
    try:
        return json.loads(run.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {}
