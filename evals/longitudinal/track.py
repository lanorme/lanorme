"""Track code quality across successive turns on the same growing codebase.

The readability and articulacy evals both sample the favourable case: one file,
written once, from a clear specification. Neither answers the question that
actually worries people, which is whether quality decays as an agent works on a
codebase it half-remembers, turn after turn.

This measures that. Each project is built over three turns, every turn
extending the previous turn's code, and the tree is snapshotted after each. The
output is a slope, not a score.

Findings are reported **per 1000 effective lines**. A raw count rises simply
because the codebase grew, so an unnormalised table would show "decay" for a
project whose quality never moved. The normalised rate is what carries a claim.

    uv run python evals/longitudinal/track.py snapshot <project> <turn>
    uv run python evals/longitudinal/track.py report
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "readability"))

from metrics import measure  # noqa: E402

RUNS = HERE / "runs" / "sonnet"
SNAPSHOTS = HERE / "snapshots"
REPORT = HERE / "report.json"

# Stock defaults plus the four opinionated rules this work added, so the table
# shows both what a new project sees and what the readability rules see.
CONFIG = """ignore = ["LAYER", "PORT", "TERM", "DOCS", "SKILL"]
[docstrings]
enabled = true
[naming_scope]
enabled = true
[suppressions]
enabled = true
max_total = 0
"""


@dataclass(frozen=True)
class TurnStats:
    """Everything measured about one snapshot of one project."""

    project: str
    turn: int
    files: int
    lines: int
    findings: dict[str, int]
    docstring_coverage: float
    undocumented: int
    comments_per_100: float
    short_name_rate: float

    def per_kloc(self, *, code: str) -> float:
        """Findings of *code* per 1000 effective lines, so turns are comparable."""
        if not self.lines:
            return 0.0
        return round(1000 * self.findings.get(code, 0) / self.lines, 2)


def _lint(*, tree: Path) -> dict[str, int]:
    """Run LaNorme over a copy of *tree*, returning a tally by rule code."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / tree.name
        shutil.copytree(tree, target, ignore=shutil.ignore_patterns("__pycache__"))
        (Path(tmp) / "lanorme.toml").write_text(CONFIG, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "lanorme", "check", ".", "--output-format", "ndjson"],
            cwd=tmp, capture_output=True, text=True, check=False,
        )
    tally: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        code = str(json.loads(line).get("rule", "?")).split(":")[0]
        tally[code] = tally.get(code, 0) + 1
    return tally


def _measure_tree(*, tree: Path, project: str, turn: int) -> TurnStats:
    """Measure one snapshot: LaNorme findings plus the readability yardstick."""
    files = [f for f in sorted(tree.rglob("*.py")) if "__pycache__" not in f.parts]
    per_file = [measure(path=f, root=tree).as_dict() for f in files]
    lines = sum(int(m["effective_lines"]) for m in per_file)
    defined = sum(int(m["undocumented_definitions"]) for m in per_file)
    documented = [float(m["docstring_coverage"]) for m in per_file if m["effective_lines"]]
    comments = sum(float(m["comments_per_100_lines"]) * int(m["effective_lines"]) for m in per_file)
    shorts = sum(float(m["short_name_rate"]) * int(m["effective_lines"]) for m in per_file)
    return TurnStats(
        project=project,
        turn=turn,
        files=len(files),
        lines=lines,
        findings=_lint(tree=tree),
        docstring_coverage=round(sum(documented) / len(documented), 3) if documented else 0.0,
        undocumented=defined,
        comments_per_100=round(comments / lines, 2) if lines else 0.0,
        short_name_rate=round(shorts / lines, 4) if lines else 0.0,
    )


def snapshot(*, project: str, turn: int) -> None:
    """Freeze the current state of *project* as its turn-*turn* snapshot."""
    source = RUNS / project
    target = SNAPSHOTS / project / f"turn{turn}"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"snapshot {project} turn {turn} -> {target.relative_to(HERE.parent.parent)}")


def _rows() -> list[TurnStats]:
    """Measure every snapshot on disk, in project then turn order."""
    rows: list[TurnStats] = []
    for project_dir in sorted(p for p in SNAPSHOTS.iterdir() if p.is_dir()):
        for turn_dir in sorted(project_dir.iterdir()):
            if turn_dir.is_dir() and turn_dir.name.startswith("turn"):
                turn = int(turn_dir.name.removeprefix("turn"))
                rows.append(_measure_tree(tree=turn_dir, project=project_dir.name, turn=turn))
    return rows


def report() -> None:
    """Print the per-turn table and write the machine-readable report."""
    rows = _rows()
    header = (
        f"{'project':14} {'turn':>4} {'files':>5} {'lines':>6} {'doc%':>5} {'undoc':>6} "
        f"{'cmt/100':>7} {'short%':>6} {'CMT006/k':>8} {'CMT007/k':>8} {'NAM005/k':>8} {'other/k':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        other = sum(v for k, v in row.findings.items() if not k.startswith(("CMT-006", "CMT-007", "NAMING-005")))
        other_rate = round(1000 * other / row.lines, 2) if row.lines else 0.0
        print(
            f"{row.project:14} {row.turn:>4} {row.files:>5} {row.lines:>6} "
            f"{row.docstring_coverage * 100:>4.0f}% {row.undocumented:>6} "
            f"{row.comments_per_100:>7.1f} {row.short_name_rate * 100:>5.1f}% "
            f"{row.per_kloc(code='CMT-006'):>8} {row.per_kloc(code='CMT-007'):>8} "
            f"{row.per_kloc(code='NAMING-005'):>8} {other_rate:>7}"
        )
    REPORT.write_text(
        json.dumps([{**row.__dict__} for row in rows], indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nwritten: {REPORT.relative_to(HERE.parent.parent)}")


def main(argv: list[str]) -> int:
    """Dispatch the snapshot and report subcommands."""
    if len(argv) == 3 and argv[0] == "snapshot":
        snapshot(project=argv[1], turn=int(argv[2]))
        return 0
    if len(argv) == 1 and argv[0] == "report":
        report()
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
