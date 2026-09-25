"""Record a per-release evaluation audit as a single result JSON.

The audit first validates every labelled corpus (``validate_corpora.py``: every
file and every comment labelled, every file in its split, provenance present),
then captures the deterministic ACCURACY of every scorer (each
``evals/score_*.py`` exposing ``score()``) per split: combined, dev, holdout and
the dev-minus-holdout gap. It stamps the run with version and hardware
METADATA, including the git commit and dirty flag that pin the exact dataset
and code, and, unless skipped, a best-effort PERFORMANCE sweep that reuses the
pinned end-to-end corpora. The JSON is committed under ``evals/results/`` as
the audit trail for a release.

With ``--gate PREVIOUS.json`` the audit also fails when any rule's HOLDOUT
precision or recall drops below the previous result minus the tolerance; dev
numbers are informational and never gate (see ``regression_gate.py``).

Usage:
    uv run python evals/audit.py --version X.Y.Z [--no-perf] [--output PATH]
        [--gate PREVIOUS.json|latest] [--tolerance 0.02]

Every flag is described by ``--help``. The run is non-interactive (no prompts). Progress and diagnostics go to stderr;
a concise one-line-per-rule summary goes to stdout.

Exit codes:
    0   success: every corpus valid, every scorer produced metrics, no regression.
    1   an invalid corpus, a scorer error, or a holdout regression.
    2   usage error (missing or empty --version, unreadable --gate file).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import TypedDict

from labelled_corpus import ScoreRecord
from metrics_report import format_summary_line
from regression_gate import (
    DEFAULT_TOLERANCE,
    GateOutcome,
    find_regressions,
    format_gate,
    read_baseline,
    resolve_baseline,
)
from validate_corpora import find_problems

try:
    import lanorme
except Exception:  # noqa: BLE001 -- audit still records when the package is absent
    lanorme = None


class CorpusTiming(TypedDict, total=False):
    """One corpus' performance outcome: either a timing or a skip reason."""

    files: int
    lines: int
    seconds: float
    skipped: str


class Metadata(TypedDict):
    """Version and hardware stamp recorded with every audit."""

    audited_version: str
    lanorme_version: str
    git_commit: str
    git_dirty: bool
    python_version: str
    platform: str
    processor: str
    timestamp_utc: str


class Report(TypedDict):
    """The assembled audit document written to disk."""

    metadata: Metadata
    corpus_problems: list[str]
    accuracy: list[ScoreRecord]
    gate: GateOutcome | None
    performance: dict[str, CorpusTiming]


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_RESULTS_DIR = _HERE / "results"
_PERF_RUNS = 3


def _discover_scorers() -> list[Path]:
    """Return the sorted list of ``evals/score_*.py`` module paths."""
    return sorted(_HERE.glob("score_*.py"))


def _import_module(*, path: Path) -> ModuleType:
    """Import a standalone benchmark module from its file path."""
    name = f"_audit_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _score_one(*, path: Path) -> ScoreRecord:
    """Import one scorer and call ``score()``, mapping failures to a record.

    A stale corpus (``ValueError``) or any other failure becomes an entry with
    an ``error`` key and the scorer's ``RULE`` code when it can be read.
    """
    try:
        module = _import_module(path=path)
    except Exception as exc:  # noqa: BLE001 -- a broken module is recorded, not fatal
        return {"rule": path.stem, "error": f"import failed: {exc}"}

    rule = getattr(module, "RULE", path.stem)
    try:
        metrics: ScoreRecord = module.score()
    except ValueError as exc:
        return {"rule": rule, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 -- record any scorer crash, never abort
        return {"rule": rule, "error": f"score() failed: {exc}"}
    return metrics


def _collect_accuracy(*, scorers: list[Path]) -> tuple[list[ScoreRecord], bool]:
    """Score every scorer; return the records and whether any reported an error."""
    records: list[ScoreRecord] = []
    failed = False
    for path in scorers:
        print(f"scoring {path.name} ...", file=sys.stderr)
        record = _score_one(path=path)
        if "error" in record:
            failed = True
            print(f"  error: {record['error']}", file=sys.stderr)
        records.append(record)
    return records, failed


def _collect_performance() -> dict[str, CorpusTiming]:
    """Time ``lanorme check`` over the pinned corpora, best-effort.

    Reuses ``run_benchmarks.py`` corpus preparation and timing. A corpus that
    cannot be downloaded (offline) is recorded as skipped rather than crashing
    the audit.
    """
    bench = _import_module(path=_REPO_ROOT / "benchmarks" / "run_benchmarks.py")
    corpora: dict[str, CorpusTiming] = {}
    for name, spec, _big in bench.CORPORA:
        print(f"timing {name} ...", file=sys.stderr)
        try:
            root = bench._ensure_corpus(name=name, spec=spec)
        except Exception as exc:  # noqa: BLE001 -- a download error must not abort
            corpora[name] = {"skipped": f"corpus error: {exc}"}
            continue
        if root is None:
            corpora[name] = {"skipped": "corpus unavailable (offline?)"}
            continue
        n_files, n_lines = bench._measure_corpus_size(root=root)
        seconds = bench._time_end_to_end(root=root, runs=_PERF_RUNS)
        corpora[name] = {
            "files": n_files,
            "lines": n_lines,
            "seconds": round(seconds, 4),
        }
    return corpora


def _read_git_output(*, args: list[str]) -> str:
    """Return a git command's stripped stdout, or "" if git is unavailable."""
    try:
        out = subprocess.run(["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True)
    except OSError:
        return ""
    return out.stdout.strip()


def _read_git_commit() -> str:
    """Return the short git commit hash, or 'unknown' if git is unavailable."""
    return _read_git_output(args=["rev-parse", "--short", "HEAD"]) or "unknown"


def _is_git_dirty() -> bool:
    """Return True if the working tree has uncommitted changes.

    A dirty tree means the result was produced from code or corpora that no
    recorded commit captures, so ``git_commit`` alone would not reproduce it.
    """
    return bool(_read_git_output(args=["status", "--porcelain"]))


def _read_lanorme_version() -> str:
    """Read ``__version__`` from the installed lanorme package."""
    if lanorme is None:
        return "unknown"
    return getattr(lanorme, "__version__", "unknown")


def _build_metadata(*, audited_version: str) -> Metadata:
    """Assemble the version and hardware stamp for the run."""
    return {
        "audited_version": audited_version,
        "lanorme_version": _read_lanorme_version(),
        "git_commit": _read_git_commit(),
        "git_dirty": _is_git_dirty(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def _default_output(*, version: str) -> Path:
    """Return the canonical results path for a version."""
    return _RESULTS_DIR / f"v{version}.json"


def _write_report(*, report: Report, output: Path) -> None:
    """Write the report as indented JSON, creating the directory if needed."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _print_summary(*, report: Report, output: Path, perf_enabled: bool) -> None:
    """Print a concise human summary to stdout, one line per rule."""
    for problem in report["corpus_problems"]:
        print(f"corpus: {problem}")
    for record in report["accuracy"]:
        print(format_summary_line(record=record))
    if report["gate"] is not None:
        print("\n".join(format_gate(outcome=report["gate"])))
    perf = report["performance"]
    if not perf_enabled:
        print("perf: skipped")
    else:
        timed = [n for n, d in perf.items() if "seconds" in d]
        skipped = [n for n, d in perf.items() if "skipped" in d]
        print(f"perf: timed {len(timed)}, skipped {len(skipped)}")
    print(f"written: {output}")


def _parse_args(*, argv: list[str]) -> argparse.Namespace:
    """Build the argument parser and parse ``argv``."""
    parser = argparse.ArgumentParser(
        prog="audit.py",
        description="Record a per-release benchmark audit JSON.",
        epilog=(
            "exit codes: 0 success; 1 an invalid corpus, a scorer error or a holdout "
            "regression; 2 usage error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        dest="version",
        default="",
        help="Release version being audited (required, non-empty).",
    )
    parser.add_argument(
        "--no-perf",
        dest="no_perf",
        action="store_true",
        help="Skip the performance sweep (accuracy only).",
    )
    parser.add_argument(
        "--output",
        dest="output",
        default=None,
        help="Write the JSON here instead of evals/results/v<version>.json.",
    )
    parser.add_argument(
        "--gate",
        dest="gate",
        default=None,
        help=(
            "Fail when a holdout precision or recall drops against this previous audit JSON "
            "('latest' picks the newest evals/results/v*.json)."
        ),
    )
    parser.add_argument(
        "--tolerance",
        dest="tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"How far a holdout ratio may drop before the gate fails (default {DEFAULT_TOLERANCE}).",
    )
    return parser.parse_args(argv)


def _apply_gate(
    *,
    baseline: Path | None,
    tolerance: float,
    accuracy: list[ScoreRecord],
) -> GateOutcome | None:
    """Compare the run with the baseline audit; None when no gate was asked for."""
    if baseline is None:
        return None
    return find_regressions(
        baseline=read_baseline(path=baseline),
        current=accuracy,
        tolerance=tolerance,
        baseline_name=baseline.name,
    )


def _run(*, args: argparse.Namespace) -> int:
    """Execute the audit and return the process exit code."""
    version = args.version.strip()
    if not version:
        print("error: --version X.Y.Z is required and must be non-empty.", file=sys.stderr)
        return 2
    try:
        baseline = resolve_baseline(gate=args.gate, results_dir=_RESULTS_DIR)
    except FileNotFoundError as exc:
        print(f"error: {exc}.", file=sys.stderr)
        return 2

    problems = find_problems()
    accuracy, failed = _collect_accuracy(scorers=_discover_scorers())
    gate = _apply_gate(baseline=baseline, tolerance=args.tolerance, accuracy=accuracy)
    perf_enabled = not args.no_perf
    performance = _collect_performance() if perf_enabled else {}

    report: Report = {
        "metadata": _build_metadata(audited_version=version),
        "corpus_problems": problems,
        "accuracy": accuracy,
        "gate": gate,
        "performance": performance,
    }
    output = Path(args.output) if args.output else _default_output(version=version)
    _write_report(report=report, output=output)
    _print_summary(report=report, output=output, perf_enabled=perf_enabled)
    return 1 if failed or problems or (gate and gate["regressions"]) else 0


def main(*, argv: list[str]) -> int:
    """CLI entry point."""
    return _run(args=_parse_args(argv=argv))


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
