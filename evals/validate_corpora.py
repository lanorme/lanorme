"""Check that every labelled eval corpus is complete, placed and attributed.

A scorer can only count what is labelled: an unlabelled file or comment drops
out of the confusion matrix without a trace, and a label on a line that holds
nothing is a phantom true negative or false negative. This script fails on:

- a corpus file under ``dev/`` or ``holdout/`` with no entry in ``labels.json``;
- an entry that names a missing file;
- a comment corpus (``"unit": "comment"``) with a comment no label covers, or a
  label on a line that holds no comment;
- a definition corpus with a label off a ``def`` / ``class`` line, and a line
  corpus with a label on a blank or out-of-range line;
- missing or malformed provenance (``source``, ``labelled_by``,
  ``labelled_before_rule``);
- a file in the wrong split: a hand-labelled file must sit where the hash of
  its name puts it, generated files only in ``holdout/generated/``, and a corpus
  must be split exactly when it is large enough.

Usage:
    uv run python evals/validate_corpora.py

Exit codes: 0 every corpus is valid; 1 a problem was found (each is listed).
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

from labelled_corpus import (
    CORPORA_ROOT,
    GENERATED_PREFIX,
    MIN_FILES_PER_SIDE,
    SPLITS,
    UNITS,
    FileEntry,
    LabelsDocument,
    assign_split,
    find_corpus_files,
    read_labels,
)

_SOURCE = re.compile(r"^(hand-written|mined:[^@\s]+@[0-9A-Za-z._-]+|generated:[a-z_]+)$")
_SPLIT_STATES = frozenset({"hashed", "too_small"})


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
        problems.extend(find_entry_problems(path=path, entry=entry, split=document["split"]))
        problems.extend(
            find_site_problems(path=path, entry=entry, document=document, corpus=corpus),
        )
    problems.extend(find_split_problems(document=document))
    return [f"{corpus.name}: {problem}" for problem in problems]


def find_header_problems(*, document: LabelsDocument) -> list[str]:
    """Check the top-level keys a scorer and this validator depend on."""
    problems: list[str] = []
    if not isinstance(document.get("files"), dict):
        problems.append('labels.json has no "files" map')
    if document.get("unit") not in UNITS:
        problems.append(f'"unit" must be one of {sorted(UNITS)}')
    if document.get("split") not in _SPLIT_STATES:
        problems.append(f'"split" must be one of {sorted(_SPLIT_STATES)}')
    if not document.get("rules"):
        problems.append('"rules" must name the rule codes scored on this corpus')
    return problems


def find_entry_problems(*, path: str, entry: FileEntry, split: str) -> list[str]:
    """Check one file's provenance fields and its placement in the split."""
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
    generated = source.startswith("generated:")
    if generated != path.startswith(GENERATED_PREFIX):
        problems.append(f"{path}: generated files, and only they, belong in {GENERATED_PREFIX}")
    elif not generated:
        problems.extend(find_placement_problems(path=path, split=split))
    return problems


def find_placement_problems(*, path: str, split: str) -> list[str]:
    """Check a hand-labelled file sits in the split its name hashes to."""
    side, _, inner = path.partition("/")
    if side not in SPLITS:
        return [f"{path}: must sit under dev/ or holdout/"]
    want = "dev" if split == "too_small" else assign_split(name=inner)
    if side != want:
        return [f"{path}: sits in {side}/ but its name assigns it to {want}/"]
    return []


def find_split_problems(*, document: LabelsDocument) -> list[str]:
    """Check the corpus is split exactly when the hash split is large enough."""
    inner_names = [
        path.partition("/")[2]
        for path, entry in document["files"].items()
        if not entry.get("source", "").startswith("generated:")
    ]
    held = sum(1 for name in inner_names if assign_split(name=name) == "holdout")
    too_small = min(held, len(inner_names) - held) < MIN_FILES_PER_SIDE
    want = "too_small" if too_small else "hashed"
    if document["split"] != want:
        return [
            f'"split" is {document["split"]!r} but the hash split gives '
            f"{len(inner_names) - held} dev / {held} holdout files, so it must be {want!r}",
        ]
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


def main() -> int:
    """Print every problem and return 1 if there is any, else 0."""
    problems = find_problems()
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} corpus problem(s) found.", file=sys.stderr)
        return 1
    print("every corpus is complete, placed and attributed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
