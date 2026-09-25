"""Check that every labelled eval corpus is complete, placed, attributed and in step.

A scorer can only count what is labelled: an unlabelled file or comment drops
out of the confusion matrix without a trace, and a label on a line that holds
nothing is a phantom true negative or false negative. This script fails on:

- a corpus file under ``dev/`` or ``holdout/`` with no entry in ``labels.json``;
- an entry that names a missing file;
- a comment corpus (``"unit": "comment"``) with a comment no label covers, or a
  label on a line that holds no comment;
- a definition corpus with a label off a ``def`` / ``class`` line, and a line
  corpus with a label on a blank or out-of-range line;
- a label whose ``line_hash`` no longer matches the text of its line (a line
  inserted above shifted it, or the line was edited), or that has none;
- a positive label under ``negatives/``, or a ``positives/`` file with none;
- missing or malformed provenance (``source``, ``labelled_by``,
  ``labelled_before_rule``);
- a file whose directory differs from the ``split`` its entry records, a
  missing ``split``, or a generated file outside ``holdout/generated/``.

``--stamp`` fills in what a new file's entry leaves out: its ``split`` (the
split its name proposes, see ``labelled_corpus.compute_proposed_split``) and each
label's ``line_hash`` (from the line the label names today). It never
overwrites a recorded value, so a label that drifted stays caught.

Usage:
    uv run python evals/validate_corpora.py           # report every problem
    uv run python evals/validate_corpora.py --stamp   # fill missing split and line_hash

Exit codes: 0 every corpus is valid; 1 a problem was found (each is listed).
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

from labelled_corpus import (
    CORPORA_ROOT,
    GENERATED_PREFIX,
    SPLITS,
    UNITS,
    FileEntry,
    LabelsDocument,
    SiteLabel,
    compute_proposed_split,
    find_corpus_files,
    hash_line,
    read_labels,
    write_labels,
)

_SOURCE = re.compile(r"^(hand-written|mined:[^@\s]+@[0-9A-Za-z._-]+|generated:[a-z_]+)$")


def find_problems(*, corpora_root: Path = CORPORA_ROOT) -> list[str]:
    """Return every problem across all corpora under *corpora_root*."""
    problems: list[str] = []
    for corpus in sorted(path for path in corpora_root.iterdir() if path.is_dir()):
        problems.extend(find_corpus_problems(corpus=corpus))
    return problems


def find_corpus_problems(*, corpus: Path) -> list[str]:
    """Return the problems of one corpus, each prefixed with its name."""
    if not (corpus / "labels.json").is_file():
        return [f"{corpus.name}: no labels.json"]
    document = read_labels(corpus=corpus)
    problems = find_header_problems(document=document)
    if problems:
        return [f"{corpus.name}: {problem}" for problem in problems]
    files = set(find_corpus_files(corpus=corpus))
    labelled = set(document["files"])
    problems.extend(f"{path}: corpus file has no label entry" for path in sorted(files - labelled))
    problems.extend(f"{path}: label names a missing file" for path in sorted(labelled - files))
    for path in sorted(files & labelled):
        entry = document["files"][path]
        problems.extend(find_entry_problems(path=path, entry=entry))
        problems.extend(
            find_site_problems(path=path, entry=entry, document=document, corpus=corpus),
        )
    return [f"{corpus.name}: {problem}" for problem in problems]


def find_header_problems(*, document: LabelsDocument) -> list[str]:
    """Check the top-level keys a scorer and this validator depend on."""
    problems: list[str] = []
    if not isinstance(document.get("files"), dict):
        problems.append('labels.json has no "files" map')
    if document.get("unit") not in UNITS:
        problems.append(f'"unit" must be one of {sorted(UNITS)}')
    if not document.get("rules"):
        problems.append('"rules" must name the rule codes scored on this corpus')
    return problems


def find_entry_problems(*, path: str, entry: FileEntry) -> list[str]:
    """Check one file's provenance fields and its placement in its recorded split."""
    problems: list[str] = []
    source = entry.get("source", "")
    if not _SOURCE.match(source):
        problems.append(
            f"{path}: source {source!r} is not hand-written, mined:<repo@sha> "
            "or generated:<transform>",
        )
    if not entry.get("labelled_by"):
        problems.append(f"{path}: labelled_by is missing")
    if entry.get("labelled_before_rule") not in (True, False, "unknown"):
        problems.append(f'{path}: labelled_before_rule must be true, false or "unknown"')
    if source.startswith("generated:") != path.startswith(GENERATED_PREFIX):
        problems.append(f"{path}: generated files, and only they, belong in {GENERATED_PREFIX}")
    problems.extend(find_placement_problems(path=path, entry=entry))
    problems.extend(find_polarity_problems(path=path, entry=entry))
    return problems


def find_placement_problems(*, path: str, entry: FileEntry) -> list[str]:
    """Check a file sits under the split its entry records."""
    side, _, inner = path.partition("/")
    split = entry.get("split")
    if split is None:
        return [
            f"{path}: no split recorded (its name proposes {compute_proposed_split(name=inner)}/); "
            "run validate_corpora.py --stamp",
        ]
    if split not in SPLITS:
        return [f"{path}: split {split!r} must be one of {list(SPLITS)}"]
    if side != split:
        return [f"{path}: sits in {side}/ but labels.json records it in {split}/"]
    return []


def find_polarity_problems(*, path: str, entry: FileEntry) -> list[str]:
    """Check no positive label sits under negatives/ and every positives/ file has one."""
    parts = path.split("/")
    if "labels" in entry:
        flags = [label.get("flag") is True for label in entry["labels"]]
    elif isinstance(entry.get("flag"), dict):
        # A per-rule flag: the file is positive when any rule of the corpus flags it.
        flags = [value is True for value in entry["flag"].values()]
    else:
        flags = [entry.get("flag") is True]
    if "negatives" in parts and any(flags):
        return [f"{path}: a file under negatives/ carries a positive label"]
    if "positives" in parts and not any(flags):
        return [f"{path}: a file under positives/ has no positive label"]
    return []


def find_site_problems(
    *,
    path: str,
    entry: FileEntry,
    document: LabelsDocument,
    corpus: Path,
) -> list[str]:
    """Check the labels of one file fit the corpus unit and the file's content."""
    unit = document["unit"]
    if unit == "file":
        return find_file_flag_problems(path=path, entry=entry, rules=document["rules"])
    labels = entry.get("labels")
    if not labels or "flag" in entry:
        return [f'{path}: a {unit}-unit entry needs a non-empty "labels" list']
    lines = [int(label.get("line", 0)) for label in labels]
    problems = [
        f"{path}:{label.get('line')}: label needs an integer line and a boolean flag"
        for label in labels
        if not isinstance(label.get("line"), int) or not isinstance(label.get("flag"), bool)
    ]
    problems.extend(
        f"{path}:{line}: labelled twice"
        for line in sorted({n for n in lines if lines.count(n) > 1})
    )
    text = (corpus / path).read_text(encoding="utf-8")
    problems.extend(find_line_problems(path=path, text=text, unit=unit, lines=set(lines)))
    problems.extend(find_drift_problems(path=path, text=text, labels=labels))
    return problems


def find_drift_problems(*, path: str, text: str, labels: list[SiteLabel]) -> list[str]:
    """Check each label's recorded ``line_hash`` still matches the text of its line."""
    source_lines = text.splitlines()
    problems: list[str] = []
    for label in labels:
        line, recorded = label.get("line"), label.get("line_hash")
        if not isinstance(line, int) or not 1 <= line <= len(source_lines):
            continue
        if recorded is None:
            problems.append(f"{path}:{line}: label has no line_hash; run --stamp")
            continue
        if hash_line(text=source_lines[line - 1]) != recorded:
            moved = [
                number
                for number, source in enumerate(source_lines, start=1)
                if hash_line(text=source) == recorded
            ]
            where = f"; its text is now on line {moved[0]}" if len(moved) == 1 else ""
            problems.append(
                f"{path}:{line}: the labelled line's text changed since it was labelled{where}",
            )
    return problems


def find_file_flag_problems(*, path: str, entry: FileEntry, rules: list[str]) -> list[str]:
    """Check a file-unit entry's flag: one boolean, or one boolean per scored rule."""
    flag = entry.get("flag")
    if "labels" in entry or not isinstance(flag, (bool, dict)):
        return [f'{path}: a file-unit entry needs a boolean "flag" and no "labels"']
    if isinstance(flag, dict) and (
        set(flag) != set(rules) or not all(isinstance(value, bool) for value in flag.values())
    ):
        return [f'{path}: a per-rule "flag" needs one boolean for each of {sorted(rules)}']
    return []


def find_line_problems(*, path: str, text: str, unit: str, lines: set[int]) -> list[str]:
    """Check labelled lines against what the file holds on them."""
    if unit == "comment":
        comments = collect_comment_lines(text=text)
        problems = [f"{path}:{n}: comment has no label" for n in sorted(comments - lines)]
        problems.extend(
            f"{path}:{n}: label is on a line with no comment" for n in sorted(lines - comments)
        )
        return problems
    if unit == "definition":
        definitions = collect_definition_lines(text=text)
        return [
            f"{path}:{n}: label is not on a def or class line" for n in sorted(lines - definitions)
        ]
    source_lines = text.splitlines()
    return [
        f"{path}:{n}: label is on a blank or missing line"
        for n in sorted(lines)
        if not 1 <= n <= len(source_lines) or not source_lines[n - 1].strip()
    ]


def collect_comment_lines(*, text: str) -> set[int]:
    """Return the line of every ``#`` comment token in a Python source."""
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return {token.start[0] for token in tokens if token.type == tokenize.COMMENT}


def collect_definition_lines(*, text: str) -> set[int]:
    """Return the ``def`` / ``class`` line of every definition in a Python source."""
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return {node.lineno for node in ast.walk(ast.parse(text)) if isinstance(node, kinds)}


def stamp_corpus(*, corpus: Path) -> int:
    """Fill each entry's missing ``split`` and each label's missing ``line_hash``.

    Returns how many values were written. A recorded value is never replaced.
    """
    document = read_labels(corpus=corpus)
    written = 0
    for path, entry in document["files"].items():
        if "split" not in entry:
            inner = path.partition("/")[2]
            proposed = (
                "holdout"
                if path.startswith(GENERATED_PREFIX)
                else compute_proposed_split(name=inner)
            )
            document["files"][path] = {"split": proposed, **entry}
            written += 1
        if "labels" in entry and (corpus / path).is_file():
            text = (corpus / path).read_text(encoding="utf-8")
            written += stamp_line_hashes(text=text, labels=entry["labels"])
    if written:
        write_labels(corpus=corpus, document=document)
    return written


def stamp_line_hashes(*, text: str, labels: list[SiteLabel]) -> int:
    """Give each label without a ``line_hash`` the hash of its line; return how many."""
    source_lines = text.splitlines()
    written = 0
    for label in labels:
        line = label.get("line")
        if "line_hash" not in label and isinstance(line, int) and 1 <= line <= len(source_lines):
            label["line_hash"] = hash_line(text=source_lines[line - 1])
            written += 1
    return written


def main(*, argv: list[str]) -> int:
    """Stamp when asked, then print every problem and return 1 if there is any, else 0."""
    parser = argparse.ArgumentParser(prog="validate_corpora.py", description=__doc__)
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="Fill each entry's missing split and each label's missing line_hash first.",
    )
    args = parser.parse_args(argv)
    if args.stamp:
        for corpus in sorted(path for path in CORPORA_ROOT.iterdir() if path.is_dir()):
            if (corpus / "labels.json").is_file():
                written = stamp_corpus(corpus=corpus)
                if written:
                    print(f"{corpus.name}: stamped {written} value(s)")
    problems = find_problems()
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} corpus problem(s) found.", file=sys.stderr)
        return 1
    print("every corpus is complete, placed, attributed and in step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
