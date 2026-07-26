"""Score generated code for readability, and for what LaNorme says about it.

For every generated sample under ``runs/<model>/<task>/`` this runs LaNorme
twice, once with the stock defaults a new project gets and once with every
opinionated rule enabled, then measures the sample with the yardstick in
``metrics.py``. The output is a single JSON: what the tool reported, next to
what a reviewer would object to.

    uv run python evals/readability/run_eval.py [--model sonnet]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import measure  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
CONFIGS = {"default": HERE / "lanorme_default.toml", "max": HERE / "lanorme_max.toml"}


def _lint(*, sample: Path, config: Path) -> list[dict[str, object]]:
    """Run LaNorme over a copy of *sample* governed by *config*, return findings."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / sample.name
        shutil.copytree(sample, target)
        shutil.copy(config, Path(tmp) / "lanorme.toml")
        proc = subprocess.run(
            [sys.executable, "-m", "lanorme", "check", ".", "--output-format", "ndjson"],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]


def _rule_codes(*, findings: list[dict[str, object]]) -> dict[str, int]:
    """Tally findings by rule code, so runs are comparable at a glance."""
    tally: dict[str, int] = {}
    for finding in findings:
        code = str(finding.get("rule", "?")).split(":")[0]
        tally[code] = tally.get(code, 0) + 1
    return tally


def _score_sample(*, sample: Path) -> dict[str, object]:
    """Lint one generated sample under both configs and measure its readability."""
    lint = {
        name: _rule_codes(findings=_lint(sample=sample, config=path))
        for name, path in CONFIGS.items()
    }
    return {
        "task": sample.name,
        "lanorme": lint,
        "readability": [measure(path=f, root=sample).as_dict() for f in sorted(sample.rglob("*.py"))],
    }


def main() -> int:
    """Score every generated sample for one model and write the report JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="sonnet", help="Subdirectory of runs/ to score.")
    args = parser.parse_args()

    model_dir = RUNS / args.model
    if not model_dir.is_dir():
        print(f"No generated samples at {model_dir}", file=sys.stderr)
        return 1

    samples = [d for d in sorted(model_dir.iterdir()) if d.is_dir()]
    report = {"model": args.model, "samples": [_score_sample(sample=s) for s in samples]}
    out = HERE / f"report_{args.model}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Scored {len(samples)} samples to {out.relative_to(HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
