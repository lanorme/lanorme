"""Size and complexity of a snapshot: radon cc, mi and raw, plus ast function lengths.

Files are split into groups by :func:`classify_file`: ``app`` (the
application package, tests excluded), ``tests`` (test modules and conftest
files anywhere) and ``other`` (any remaining project Python, such as scripts).
Each metric is reported per group.
"""

import ast
import json
import re
import statistics
from pathlib import Path

from measure_tools import run_uvx

SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", "build", "dist"}
TEST_DIRS = {"tests", "test"}
GROUPS = ("app", "tests", "other")
POOR_RANKS = {"C", "D", "E", "F"}
LONG_FUNCTION = 50
WORST_COUNT = 5
SUPPRESSIONS = {
    "noqa": re.compile(r"#\s*noqa\b", re.IGNORECASE),
    "type_ignore": re.compile(r"#\s*type:\s*ignore"),
    "nosec": re.compile(r"#\s*nosec\b"),
    "pragma_no_cover": re.compile(r"#\s*pragma:\s*no\s*cover"),
    "pylint_disable": re.compile(r"#\s*pylint:\s*disable"),
    "lanorme_ignore": re.compile(r"#\s*lanorme\s*:\s*ignore"),
}


def classify_file(rel: Path) -> str:
    """The group of a project-relative Python file: app, tests or other."""
    name = rel.name
    is_test = (
        any(part in TEST_DIRS for part in rel.parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )
    if is_test:
        return "tests"
    if rel.parts[0] == "app":
        return "app"
    return "other"


def collect_python_files(root: Path) -> dict[str, list[Path]]:
    """The project's own Python files, relative to ``root``, by group."""
    groups: dict[str, list[Path]] = {group: [] for group in GROUPS}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        groups[classify_file(rel)].append(rel)
    return groups


def measure_size(root: Path, *, files: dict[str, list[Path]]) -> dict[str, object]:
    """Radon cc/mi/raw, function lengths and suppression comments, per group."""
    every = [str(rel) for group in GROUPS for rel in files[group]]
    radon = {kind: _read_radon(kind, root=root, files=every) for kind in ("cc", "mi", "raw")}
    result: dict[str, object] = {"radon_errors": _collect_radon_errors(radon)}
    for group in GROUPS:
        names = {str(rel) for rel in files[group]}
        result[group] = {
            "raw": _summarise_raw(radon["raw"], names=names),
            "cc": _summarise_cc(radon["cc"], names=names),
            "mi": _summarise_mi(radon["mi"], names=names),
            "functions": _summarise_function_lengths(root, files=files[group]),
            "suppressions": _count_suppressions(root, files=files[group]),
        }
    return result


def _read_radon(kind: str, *, root: Path, files: list[str]) -> dict[str, object]:
    if not files:
        return {}
    run = run_uvx("radon", args=[kind, "-j", *files], cwd=root)
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError:
        return {"__failed__": run.build_error_tail()}


def _collect_radon_errors(radon: dict[str, dict[str, object]]) -> list[str]:
    errors = []
    for kind, data in radon.items():
        for name, value in data.items():
            if name == "__failed__":
                errors.append(f"radon {kind}: {value}")
            elif isinstance(value, dict) and "error" in value:
                errors.append(f"radon {kind} {name}: {value['error']}")
    return errors


def _summarise_raw(raw: dict[str, object], *, names: set[str]) -> dict[str, int]:
    keys = ("loc", "sloc", "lloc", "comments", "multi", "blank")
    totals = dict.fromkeys(keys, 0)
    for name in names:
        entry = raw.get(name)
        if isinstance(entry, dict) and "error" not in entry:
            for key in keys:
                totals[key] += entry.get(key, 0)
    return {"files": len(names), **totals}


def _summarise_cc(cc: dict[str, object], *, names: set[str]) -> dict[str, object]:
    blocks = []
    for name in sorted(names):
        entries = cc.get(name)
        if isinstance(entries, list):
            blocks.extend(_flatten_functions(name, entries=entries))
    scores = [block["cc"] for block in blocks]
    worst = sorted(blocks, key=lambda block: -block["cc"])[:WORST_COUNT]
    return {
        "functions": len(blocks),
        "mean": _round(statistics.fmean(scores)) if scores else None,
        "max": max(scores, default=None),
        "graded_c_or_worse": sum(block["rank"] in POOR_RANKS for block in blocks),
        "worst": [f"{b['file']}:{b['line']} {b['name']} {b['cc']}" for b in worst],
    }


def _flatten_functions(file: str, *, entries: list[dict]) -> list[dict]:
    """Functions, methods and closures; classes are skipped (their methods are listed too)."""
    blocks = []
    pending = [entry for entry in entries if entry.get("type") != "class"]
    while pending:
        entry = pending.pop()
        name = entry["name"]
        if entry.get("classname"):
            name = f"{entry['classname']}.{name}"
        blocks.append(
            {
                "file": file,
                "line": entry["lineno"],
                "name": name,
                "cc": entry["complexity"],
                "rank": entry["rank"],
            },
        )
        pending.extend(entry.get("closures", []))
    return blocks


def _summarise_mi(mi: dict[str, object], *, names: set[str]) -> dict[str, object]:
    scores = [
        entry["mi"] for name in names if isinstance(entry := mi.get(name), dict) and "mi" in entry
    ]
    return {
        "files": len(scores),
        "mean": _round(statistics.fmean(scores)) if scores else None,
        "min": _round(min(scores)) if scores else None,
    }


def _summarise_function_lengths(root: Path, *, files: list[Path]) -> dict[str, object]:
    lengths = []
    for rel in files:
        lengths.extend(_read_function_lengths(root / rel))
    return {
        "count": len(lengths),
        "mean_lines": _round(statistics.fmean(lengths)) if lengths else None,
        "max_lines": max(lengths, default=None),
        f"over_{LONG_FUNCTION}_lines": sum(length > LONG_FUNCTION for length in lengths),
    }


def _read_function_lengths(path: Path) -> list[int]:
    """Lines from ``def`` to the last body line, for every function and method."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef)
    return [
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, kinds) and node.end_lineno is not None
    ]


def _count_suppressions(root: Path, *, files: list[Path]) -> dict[str, int]:
    counts = dict.fromkeys(SUPPRESSIONS, 0)
    for rel in files:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        for key, pattern in SUPPRESSIONS.items():
            counts[key] += len(pattern.findall(text))
    return counts


def _round(value: float) -> float:
    return round(value, 2)
