"""Run the hidden acceptance suites against one stage snapshot.

    python run_acceptance.py SNAPSHOT_DIR --stage K [--output results.json]

A stage-K snapshot is run against the suites for stages 1 to K. The snapshot
is never modified: it is copied to a temporary directory, `uv sync` builds its
own environment there, and pytest runs the suites inside that environment with
the copy as the working directory, so `app.main` imports from the copy. The
suites themselves are copied beside it under their own pytest config, so the
snapshot's pytest settings and conftest files play no part.

The result is printed as JSON, one entry per suite:

    {"stage1": {"passed": 30, "failed": 1, "errors": 0, "failed_tests": [...]}}

When the snapshot cannot be set up, or a suite cannot even be collected, every
test in that suite counts as an error rather than the run crashing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict

HERE = Path(__file__).resolve().parent
SUPPORT_FILES = ("conftest.py", "fakes.py", "support.py")
IGNORED = shutil.ignore_patterns(
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".git",
)
SYNC_TIMEOUT = 900
TEST_TIMEOUT = 120
RUN_TIMEOUT = 1800
RUNNER_DEPS = ("pytest", "httpx", "pytest-timeout")
COLLECT_DEPS = ("pytest", "httpx", "fastapi", "langchain-core")


class Tally(TypedDict):
    """One suite's result: counts, and the ids of the tests that did not pass."""

    passed: int
    failed: int
    errors: int
    failed_tests: list[str]


def build_env() -> dict[str, str]:
    """Return a subprocess environment free of any outer virtualenv."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str],
) -> tuple[int, str]:
    """Run a command and return its exit code and combined output."""
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return -1, f"timed out after {timeout}s: {exc}"
    except OSError as exc:
        return -1, f"could not run {command[0]}: {exc}"
    return done.returncode, done.stdout + done.stderr


def stage_suites(stage: int) -> list[str]:
    """Return the suite names a stage-`stage` snapshot runs: stage1 to stageK."""
    return [f"stage{k}" for k in range(1, stage + 1)]


def copy_suites(suites: list[str], *, dest: Path) -> list[Path]:
    """Copy the suites and their support files to `dest` with an isolated pytest config."""
    dest.mkdir(parents=True)
    for name in SUPPORT_FILES:
        shutil.copy2(HERE / name, dest / name)
    (dest / "pytest.ini").write_text("[pytest]\naddopts =\n")
    paths = []
    for suite in suites:
        target = dest / f"test_{suite}.py"
        shutil.copy2(HERE / target.name, target)
        paths.append(target)
    return paths


def build_pytest_args(acceptance: Path, *, tests: list[Path], junit: Path) -> list[str]:
    """Return the pytest arguments that keep the snapshot's own config out."""
    return [
        "-c",
        str(acceptance / "pytest.ini"),
        "--rootdir",
        str(acceptance),
        "-p",
        "no:cacheprovider",
        "-q",
        "--continue-on-collection-errors",
        f"--junitxml={junit}",
        *[str(test) for test in tests],
    ]


def find_suite(testcase: ET.Element) -> str | None:
    """Return the suite a junit testcase belongs to, from its class or name."""
    label = f"{testcase.get('classname', '')}.{testcase.get('name', '')}"
    for part in label.replace("/", ".").split("."):
        if part.startswith("test_stage") and part.removeprefix("test_stage").isdigit():
            return part.removeprefix("test_")
    return None


def read_junit(junit: Path, *, suites: list[str]) -> tuple[dict[str, Tally], set[str]]:
    """Tally a junit report per suite; also return suites that failed to collect."""
    results = {suite: Tally(passed=0, failed=0, errors=0, failed_tests=[]) for suite in suites}
    broken: set[str] = set()
    for testcase in ET.parse(junit).getroot().iter("testcase"):
        suite = find_suite(testcase)
        if suite not in results:
            continue
        classname = testcase.get("classname", "")
        if not classname:
            broken.add(suite)
            continue
        test_id = f"{classname.replace('.', '/')}.py::{testcase.get('name')}"
        tally = results[suite]
        if testcase.find("failure") is not None:
            tally["failed"] += 1
            tally["failed_tests"].append(test_id)
        elif testcase.find("error") is not None:
            tally["errors"] += 1
            tally["failed_tests"].append(test_id)
        elif testcase.find("skipped") is None:
            tally["passed"] += 1
    return results, broken


def collect_test_ids(acceptance: Path, *, suite: str, env: dict[str, str]) -> list[str]:
    """List a suite's test ids in a throwaway environment, without the snapshot."""
    deps = [arg for dep in COLLECT_DEPS for arg in ("--with", dep)]
    command = [
        "uv",
        "run",
        "--no-project",
        "--python",
        "3.13",
        *deps,
        "python",
        "-m",
        "pytest",
        "--collect-only",
        "-q",
    ]
    command += [
        "-c",
        str(acceptance / "pytest.ini"),
        "--rootdir",
        str(acceptance),
        "-p",
        "no:cacheprovider",
    ]
    code, output = run_command(
        [*command, str(acceptance / f"test_{suite}.py")],
        cwd=acceptance,
        timeout=SYNC_TIMEOUT,
        env=env,
    )
    ids = [line.strip() for line in output.splitlines() if "::" in line]
    if code != 0 or not ids:
        print(f"could not collect {suite} for counting (exit {code})", file=sys.stderr)
    return ids


def build_error_tally(acceptance: Path, *, suite: str, env: dict[str, str]) -> Tally:
    """Count every test in a suite as an error."""
    ids = collect_test_ids(acceptance, suite=suite, env=env)
    return Tally(passed=0, failed=0, errors=len(ids), failed_tests=ids)


def run_suites(snapshot: Path, *, stage: int, workdir: Path) -> dict[str, Tally]:
    """Copy the snapshot, sync its environment, run the suites and tally them."""
    env = build_env()
    suites = stage_suites(stage)
    project = workdir / "project"
    acceptance = workdir / "acceptance"
    junit = workdir / "junit.xml"
    shutil.copytree(snapshot, project, ignore=IGNORED, symlinks=True)
    tests = copy_suites(suites, dest=acceptance)

    code, output = run_command(["uv", "sync"], cwd=project, timeout=SYNC_TIMEOUT, env=env)
    if code != 0:
        print(f"uv sync failed (exit {code}):\n{output[-4000:]}", file=sys.stderr)
        return {suite: build_error_tally(acceptance, suite=suite, env=env) for suite in suites}

    deps = [arg for dep in RUNNER_DEPS for arg in ("--with", dep)]
    command = [
        "uv",
        "run",
        *deps,
        "python",
        "-m",
        "pytest",
        *build_pytest_args(acceptance, tests=tests, junit=junit),
    ]
    command.append(f"--timeout={TEST_TIMEOUT}")
    env["PYTHONPATH"] = str(project)
    code, output = run_command(command, cwd=project, timeout=RUN_TIMEOUT, env=env)
    print(output[-6000:], file=sys.stderr)
    if not junit.exists():
        print(f"pytest wrote no report (exit {code})", file=sys.stderr)
        return {
            suite: build_error_tally(acceptance, suite=suite, env=build_env()) for suite in suites
        }
    results, broken = read_junit(junit, suites=suites)
    for suite in broken:
        results[suite] = build_error_tally(acceptance, suite=suite, env=build_env())
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "snapshot",
        type=Path,
        help="a stage snapshot: a uv project with an app/ package",
    )
    parser.add_argument(
        "--stage",
        type=int,
        required=True,
        choices=(1, 2, 3),
        help="run the suites for stages 1..K",
    )
    parser.add_argument("--output", type=Path, help="also write the JSON result to this file")
    args = parser.parse_args(argv)
    if not args.snapshot.is_dir():
        parser.error(f"{args.snapshot} is not a directory")

    with tempfile.TemporaryDirectory(prefix="agent-ab-acceptance-") as tmp:
        results = run_suites(args.snapshot.resolve(), stage=args.stage, workdir=Path(tmp))
    text = json.dumps(results, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
