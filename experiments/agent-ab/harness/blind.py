"""Build anonymised A/B pairs of stage snapshots for the blind judge.

    python blind.py RUNS_DIR OUT_DIR [--seed N]

For every stage k and run index n, copies control-n/stage<k> and
lanorme-n/stage<k> into OUT_DIR/stage<k>-run<n>/A and .../B in a seeded random
order, with everything that names LaNorme removed: the project CLAUDE.md (the
arms differ there by design), lanorme.toml, any [tool.lanorme] table, and any
line elsewhere mentioning LaNorme. The arm behind each letter goes to
OUT_DIR.key.json, beside OUT_DIR rather than in it, so the judge cannot reach it.
"""

import argparse
import json
import random
import re
import shutil
from pathlib import Path

DROPPED_FILES = ("CLAUDE.md", "lanorme.toml", ".lanorme-baseline.json")
LANORME_TABLE = re.compile(r"^\[tool\.lanorme[^\]]*\]\n(?:(?!\[).*\n?)*", re.MULTILINE)
COMMENT_NAMING_LANORME = re.compile(r"\s*#[^\n]*lanorme[^\n]*", re.IGNORECASE)
TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".cfg", ".ini", ".yaml", ".yml"}


def strip_lanorme_text(path: Path) -> list[str]:
    """Remove every trace of LaNorme from one text file, in place.

    In a Python file only comments are removed, since dropping a code line
    would break the program; any mention left in code is returned for a
    person to review. Other files lose every line that mentions LaNorme.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.name == "pyproject.toml":
        text = LANORME_TABLE.sub("", text)
    if path.suffix == ".py":
        text = COMMENT_NAMING_LANORME.sub("", text)
        path.write_text(text, encoding="utf-8")
        return [line for line in text.splitlines() if "lanorme" in line.lower()]
    kept = [line for line in text.splitlines(keepends=True) if "lanorme" not in line.lower()]
    path.write_text("".join(kept), encoding="utf-8")
    return []


def copy_blinded(*, source: Path, target: Path) -> None:
    """Copy one snapshot to target with LaNorme removed, warning on any mention left in code."""
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__"))
    for name in DROPPED_FILES:
        (target / name).unlink(missing_ok=True)
    for path in target.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            for line in strip_lanorme_text(path):
                print(
                    f"warning: LaNorme still named in code, review by hand: {path}: {line.strip()}",
                )


def build_pairs(*, runs: Path, out: Path, seed: int) -> dict[str, dict[str, str]]:
    """Write every A/B pair and return the key mapping pair to arm per letter."""
    rng = random.Random(seed)
    key: dict[str, dict[str, str]] = {}
    for control in sorted(runs.glob("control-*/stage*")):
        if not control.is_dir():
            continue
        run_index = control.parent.name.removeprefix("control-")
        treated = runs / f"lanorme-{run_index}" / control.name
        if not treated.is_dir():
            continue
        pair = f"{control.name}-run{run_index}"
        arms = [("control", control), ("lanorme", treated)]
        rng.shuffle(arms)
        for letter, (arm, source) in zip("AB", arms, strict=True):
            copy_blinded(source=source, target=out / pair / letter)
            key.setdefault(pair, {})[letter] = arm
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seed", type=int, default=20261001)
    args = parser.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    key = build_pairs(runs=args.runs, out=args.out, seed=args.seed)
    key_path = args.out.with_name(f"{args.out.name}.key.json")
    key_path.write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    print(f"{len(key)} pairs written to {args.out}")


if __name__ == "__main__":
    main()
