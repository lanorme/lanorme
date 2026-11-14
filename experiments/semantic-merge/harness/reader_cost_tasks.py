"""Build and check the tasks of the reader-cost experiment.

Does a finely split layout cost the next change more than a flat one? The
same codebase is prepared in two layouts: as its agent wrote it (``split``),
and with every package SHALLOW-001 could ask about collapsed into one module,
the code moved verbatim (``flat``). The same bug is planted in both, an agent
is given the same report, and the hidden acceptance suite of the agent A/B
experiment says whether the fix worked. The effort each agent spent is the
cost of reading the layout.

Three steps, each a subcommand:

- ``verify``: the two layouts of each run hold the same top-level
  definitions with identical bodies, so only the files differ.
- ``build``: one task directory per bug and layout, under a name that does
  not give the layout away, each with the bug planted and its prompt written.
  Every bug must fail the same hidden tests in both layouts.
- ``check``: after the agents have worked, run the hidden suite on every task
  directory and write one row per task, with the effort recorded for it.

Run:
    uv run python experiments/semantic-merge/harness/reader_cost_tasks.py \
        build --layouts /path/to/layouts --tasks /path/to/tasks --python /path/to/python
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
RUNNER = EXPERIMENT.parent / "agent-ab" / "acceptance" / "run_acceptance.py"
LAYOUTS = ("split", "flat")
STAGE = "3"
_IGNORED = shutil.ignore_patterns(".venv", "__pycache__", "uv.lock", ".pytest_cache")


def read_bugs() -> list[dict[str, object]]:
    """The planted bugs: the text each replaces, its replacement and its report."""
    return json.loads((EXPERIMENT / "reader-cost" / "bugs.json").read_text(encoding="utf-8"))


def list_definitions(root: Path) -> dict[str, str]:
    """Every top-level function, class and assignment under ``app/``, by name, as its syntax."""
    found: dict[str, str] = {}
    for path in sorted(root.glob("app/**/*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            match node:
                case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                    found[name] = ast.dump(node)
                case ast.ClassDef(name=name):
                    found[name] = ast.dump(node)
                case ast.Assign(targets=[ast.Name(id=name)], value=value):
                    found[name] = ast.dump(value)
                case ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value):
                    found[name] = ast.dump(value)
    return found


def run_suite(project: Path) -> dict[str, object]:
    """The hidden suite's verdict on *project*: tests passed, failed, and the failing names."""
    command = [sys.executable, str(RUNNER), str(project), "--stage", STAGE]
    output = subprocess.run(command, capture_output=True, text=True, check=False).stdout
    tally = json.loads(output[output.index("{") :])
    return {
        "passed": sum(suite["passed"] for suite in tally.values()),
        "failed": sum(suite["failed"] + suite["errors"] for suite in tally.values()),
        "failed_tests": sorted(
            name.split("::")[-1] for suite in tally.values() for name in suite["failed_tests"]
        ),
    }


def plant_bug(*, layout: Path, bug: dict[str, object], destination: Path) -> str:
    """Copy *layout* to *destination* with *bug* planted; the file it was planted in."""
    shutil.copytree(layout, destination, ignore=_IGNORED)
    old, new = str(bug["old"]), str(bug["new"])
    hits = [
        path
        for path in sorted(destination.glob("app/**/*.py"))
        if old in path.read_text(encoding="utf-8")
    ]
    if len(hits) != 1 or hits[0].read_text(encoding="utf-8").count(old) != 1:
        raise ValueError(f"bug {bug['id']} does not match exactly one place in {layout}")
    hits[0].write_text(hits[0].read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    return str(hits[0].relative_to(destination))


def verify_layouts(layouts: Path) -> None:
    """Fail unless each run's two layouts hold the same definitions with the same bodies."""
    for split in sorted(layouts.glob("run*-split")):
        flat = split.with_name(split.name.replace("-split", "-flat"))
        first, second = list_definitions(split), list_definitions(flat)
        if first != second:
            raise ValueError(f"{split.name} and {flat.name} do not hold the same definitions")
        counts = [
            sum(1 for path in root.glob("app/**/*.py") if path.name != "__init__.py")
            for root in (split, flat)
        ]
        print(f"{split.name}: {len(first)} definitions, {counts[0]} modules; flat {counts[1]}")


def build_tasks(*, layouts: Path, tasks: Path, python: Path) -> None:
    """Write a task directory, a prompt and a key entry per bug and layout."""
    template = (EXPERIMENT / "prompts" / "fix_bug.md").read_text(encoding="utf-8")
    key: dict[str, dict[str, object]] = {}
    for bug in read_bugs():
        failing = []
        for layout in LAYOUTS:
            name = "t" + hashlib.sha256(f"{bug['id']}:{layout}".encode()).hexdigest()[:8]
            project = tasks / name
            planted = plant_bug(
                layout=layouts / f"run{bug['run']}-{layout}",
                bug=bug,
                destination=project,
            )
            failing.append(run_suite(project)["failed_tests"])
            key[name] = {"bug": bug["id"], "run": bug["run"], "layout": layout, "file": planted}
            prompt = template.replace("{project}", str(project))
            prompt = prompt.replace("{report}", str(bug["report"]))
            (tasks / f"{name}.md").write_text(prompt.replace("{python}", str(python)), "utf-8")
        if not failing[0] or failing[0] != failing[1]:
            raise ValueError(f"bug {bug['id']} does not fail the same tests in both layouts")
        print(f"{bug['id']}: {len(failing[0])} hidden tests fail in both layouts")
    (tasks / "key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")


def check_tasks(*, tasks: Path, usage: Path, out: Path) -> None:
    """Run the hidden suite on every task directory and write one row per task."""
    key = json.loads((tasks / "key.json").read_text(encoding="utf-8"))
    effort = json.loads(usage.read_text(encoding="utf-8"))
    rows = []
    for name, task in sorted(key.items(), key=lambda item: (item[1]["bug"], item[1]["layout"])):
        verdict = run_suite(tasks / name)
        rows.append(
            {**task, **effort[name], "passed": verdict["passed"], "failed": verdict["failed"]},
        )
        print(f"{task['bug']:<10} {task['layout']:<5} failed {verdict['failed']}")
    out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    """Verify the layouts, build the tasks, or check them after the agents have worked."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("verify", "build", "check"))
    parser.add_argument("--layouts", type=Path)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--usage", type=Path)
    parser.add_argument("--out", type=Path)
    arguments = parser.parse_args()
    if arguments.step == "verify":
        verify_layouts(arguments.layouts)
    elif arguments.step == "build":
        verify_layouts(arguments.layouts)
        build_tasks(layouts=arguments.layouts, tasks=arguments.tasks, python=arguments.python)
    else:
        check_tasks(tasks=arguments.tasks, usage=arguments.usage, out=arguments.out)


if __name__ == "__main__":
    main()
