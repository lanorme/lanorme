"""Measure agent A/B snapshots with third-party tools only (never LaNorme).

    python measure.py SNAPSHOT_DIR
    python measure.py --all RUNS_DIR --out results/metrics.json [--jobs N]

A snapshot is ``runs/<arm>-<n>/stage<k>/``; its transcript and timing sit
beside it as ``stage<k>.transcript.jsonl`` and ``stage<k>.timing.json``.
Every measurement runs on a temporary copy, so the snapshot is never written
to. The analysers run through ``uvx`` at the versions pinned in
``measure_tools.PINNED``, recorded in the output.

Groups. ``app`` is the application package minus test files, ``tests`` is
every test module and conftest, ``other`` is any remaining project Python.
Size, complexity and ruff are reported per group; bandit, mypy and pylint
duplicate-code look at ``app`` only. vulture is reported twice, both counting
only findings in ``app`` files: ``app_only`` (code reached only from tests
counts as dead) and ``app_with_tests`` (tests count as usage, so only code
nothing calls is flagged). Route handlers, validators, tools and the
``create_app`` factory are whitelisted for vulture, since the framework calls
them by registration.
"""

import argparse
import json
import logging
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from measure_env import measure_mypy, measure_pytest, sync_project
from measure_lint import measure_bandit, measure_duplication, measure_ruff, measure_vulture
from measure_process import measure_transcript, read_lanorme_config
from measure_size import GROUPS, collect_python_files, measure_size
from measure_tools import build_versions

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = re.compile(r"stage(\d+)$")
RUN_NAME = re.compile(r"(?P<arm>.+)-(?P<run>\d+)$")
COPY_IGNORE = shutil.ignore_patterns(
    ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage", ".git",
)  # fmt: skip


def identify_snapshot(snapshot: Path) -> dict[str, object]:
    """The arm, run index and stage named by a ``<arm>-<n>/stage<k>`` path."""
    stage = SNAPSHOT_NAME.search(snapshot.name)
    run = RUN_NAME.search(snapshot.parent.name)
    return {
        "snapshot": str(snapshot),
        "arm": run["arm"] if run else None,
        "run": int(run["run"]) if run else None,
        "stage": int(stage.group(1)) if stage else None,
    }


def measure_snapshot(snapshot: Path) -> dict[str, object]:
    """Every metric for one snapshot, measured on a temporary copy."""
    started = time.monotonic()
    record = identify_snapshot(snapshot)
    record["process"] = _guard(
        lambda: measure_transcript(
            snapshot.parent / f"{snapshot.name}.transcript.jsonl",
            timing=snapshot.parent / f"{snapshot.name}.timing.json",
        ),
    )
    with tempfile.TemporaryDirectory(prefix="agent-ab-measure-") as temp:
        scratch = Path(temp)
        copy = scratch / "project"
        shutil.copytree(snapshot, copy, ignore=COPY_IGNORE, symlinks=True)
        record.update(_measure_copy(copy, scratch=scratch))
    record["measure_seconds"] = round(time.monotonic() - started, 1)
    return record


def _measure_copy(copy: Path, *, scratch: Path) -> dict[str, object]:
    files = collect_python_files(copy)
    size = _guard(lambda: measure_size(copy, files=files))
    sloc = {group: size.get(group, {}).get("raw", {}).get("sloc", 0) for group in GROUPS}
    record: dict[str, object] = {
        "files": {group: [str(rel) for rel in files[group]] for group in GROUPS},
        "lanorme_config": _guard(lambda: read_lanorme_config(copy)),
        "size": size,
        "ruff": _guard(lambda: measure_ruff(copy, sloc=sloc)),
        "bandit": _guard(lambda: measure_bandit(copy, app_files=files["app"])),
        "duplication": _guard(
            lambda: measure_duplication(copy, app_files=files["app"], scratch=scratch),
        ),
        "vulture": _guard(lambda: measure_vulture(copy, files=files)),
    }
    sync = _guard(lambda: sync_project(copy))
    record["sync"] = sync
    if sync.get("ok"):
        record["mypy"] = _guard(lambda: measure_mypy(copy))
        record["pytest"] = _guard(lambda: measure_pytest(copy, scratch=scratch))
    else:
        record["mypy"] = record["pytest"] = {"skipped": "uv sync failed"}
    return record


def _guard(measure: Callable[[], dict[str, object]]) -> dict[str, object]:
    """Run one measurement; a crash becomes an ``error`` record, not a lost snapshot."""
    try:
        return measure()
    except Exception as error:  # noqa: BLE001 - one broken metric must not sink the rest
        logger.exception("measurement failed")
        return {"error": f"{type(error).__name__}: {error}"}


def find_snapshots(runs: Path) -> list[Path]:
    """Every ``<arm>-<n>/stage<k>`` directory under ``runs``, in a stable order."""
    found = [
        stage
        for run in sorted(runs.iterdir())
        if run.is_dir()
        for stage in sorted(run.iterdir())
        if stage.is_dir() and SNAPSHOT_NAME.search(stage.name)
    ]
    return sorted(
        found,
        key=lambda path: (path.parent.name, int(SNAPSHOT_NAME.search(path.name).group(1))),
    )


def measure_all(runs: Path, *, out: Path, jobs: int) -> None:
    """Measure every snapshot, rewriting ``out`` after each one so progress survives a crash."""
    snapshots = find_snapshots(runs)
    document: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool_versions": build_versions(),
        "snapshots": [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for record in pool.map(measure_snapshot, snapshots):
            document["snapshots"].append(record)
            out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            logger.info("measured %s in %ss", record["snapshot"], record["measure_seconds"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshot", nargs="?", type=Path, help="one snapshot directory")
    parser.add_argument("--all", type=Path, metavar="RUNS_DIR", help="measure every snapshot")
    parser.add_argument("--out", type=Path, help="output file for --all")
    parser.add_argument("--jobs", type=int, default=2, help="snapshots measured at once (--all)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    if args.all is not None:
        if args.out is None or args.snapshot is not None:
            parser.error("--all takes RUNS_DIR and --out, and no SNAPSHOT_DIR")
        measure_all(args.all, out=args.out, jobs=args.jobs)
        return 0
    if args.snapshot is None or not args.snapshot.is_dir():
        parser.error("give a snapshot directory, or --all RUNS_DIR --out FILE")
    record = measure_snapshot(args.snapshot.resolve())
    record["tool_versions"] = build_versions()
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
