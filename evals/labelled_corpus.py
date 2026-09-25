"""Labelled eval corpora: the dev and holdout split, the labels file, and scoring.

Every corpus under ``evals/corpora/<name>/`` keeps its files in two splits:

- ``dev/`` may be read while a rule is designed and tuned;
- ``holdout/`` is sealed: a change that tunes a rule never edits that rule's
  holdout files, so the holdout numbers show how the rule generalises.

Each file's split is recorded in its ``labels.json`` entry (``"split"``) when
the file is added, and the file sits under that directory. A hash of the path
inside the split (``positives/pos_x.py``) only proposes the split for a new
file; once recorded, the split never moves, whatever the corpus grows to.
Files the adversarial generator writes live in ``holdout/generated/``.

One ``labels.json`` at the corpus root carries every label, its provenance and
a short hash of the labelled line's text (``line_hash``), so a label that
drifts off its line is caught; ``evals/README.md`` documents the schema. The
scorers call ``evaluate_corpus`` and report dev, holdout and the gap.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

CORPORA_ROOT = Path(__file__).resolve().parent / "corpora"
REPO_ROOT = CORPORA_ROOT.parent.parent
SPLITS = ("dev", "holdout")
HOLDOUT_PERCENT = 30
LINE_HASH_LENGTH = 8
GENERATED_PREFIX = "holdout/generated/"
CORPUS_SUFFIXES = frozenset({".py", ".md"})
UNITS = frozenset({"comment", "definition", "line", "file"})

# A labelled site: the path inside the corpus root, and the line (0 for a
# file-level label, where the whole file is the unit).
Site = tuple[str, int]


class SiteLabel(TypedDict, total=False):
    """One labelled line: whether the rule should flag it, and why."""

    line: int
    flag: bool
    note: str
    line_hash: str


class FileEntry(TypedDict, total=False):
    """One corpus file: its split, its provenance and its labels (per line or per file)."""

    split: str
    source: str
    labelled_by: str
    labelled_before_rule: bool | str
    added_in: str
    seed: str
    flag: bool | dict[str, bool]
    note: str
    labels: list[SiteLabel]


class LabelsDocument(TypedDict):
    """The whole ``labels.json`` of one corpus."""

    rules: list[str]
    unit: str
    description: str
    files: dict[str, FileEntry]


class Metrics(TypedDict):
    """A confusion matrix and its ratios; a ratio with no denominator is None."""

    tp: int
    fp: int
    fn: int
    tn: int
    precision: float | None
    recall: float | None
    f1: float | None


class Gap(TypedDict):
    """Dev minus holdout for each ratio: a large positive gap is overfitting."""

    precision: float | None
    recall: float | None
    f1: float | None


class ScoreRecord(TypedDict, total=False):
    """What ``score()`` returns: combined metrics plus the per-split blocks.

    The audit records a scorer that fails as ``rule`` plus ``error`` instead.
    """

    rule: str
    corpus: str
    split: str
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float | None
    recall: float | None
    f1: float | None
    dev: Metrics
    holdout: Metrics | None
    holdout_generated: Metrics
    gap: Gap | None
    error: str


@dataclass(frozen=True)
class Evaluation:
    """A scored corpus: the record, the misclassified sites and their notes."""

    record: ScoreRecord
    false_positives: list[Site]
    false_negatives: list[Site]
    notes: dict[Site, str]


def compute_proposed_split(*, name: str) -> str:
    """Return the split, ``dev`` or ``holdout``, proposed for a new file.

    *name* is the path inside the split (``positives/pos_x.py``). The first 32
    bits of its SHA-256, modulo 100, below ``HOLDOUT_PERCENT`` proposes holdout.
    It is only a proposal: the split recorded in ``labels.json`` is what counts.
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return "holdout" if int(digest[:8], 16) % 100 < HOLDOUT_PERCENT else "dev"


def hash_line(*, text: str) -> str:
    """Return the short hash a label records for its line, ignoring indentation."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:LINE_HASH_LENGTH]


def read_labels(*, corpus: Path) -> LabelsDocument:
    """Load a corpus' ``labels.json``."""
    return json.loads((corpus / "labels.json").read_text(encoding="utf-8"))


def render_labels(*, document: LabelsDocument) -> str:
    """Serialise a labels document with one label per line, sorted by path."""
    head = {key: value for key, value in document.items() if key != "files"}
    lines = ["{"]
    lines.extend(f"  {json.dumps(key)}: {json.dumps(value)}," for key, value in head.items())
    lines.append('  "files": {')
    items = sorted(document["files"].items())
    for position, (path, entry) in enumerate(items):
        lines.append(f"    {json.dumps(path)}: {{")
        fields = list(entry.items())
        for index, (key, value) in enumerate(fields):
            comma = "," if index < len(fields) - 1 else ""
            if key == "labels":
                rows = [f"        {json.dumps(label)}" for label in value]
                lines.append('      "labels": [')
                lines.append(",\n".join(rows))
                lines.append(f"      ]{comma}")
            else:
                lines.append(f"      {json.dumps(key)}: {json.dumps(value)}{comma}")
        lines.append("    }," if position < len(items) - 1 else "    }")
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def write_labels(*, corpus: Path, document: LabelsDocument) -> None:
    """Write a corpus' ``labels.json`` in the canonical layout."""
    (corpus / "labels.json").write_text(render_labels(document=document), encoding="utf-8")


def find_corpus_files(*, corpus: Path) -> list[str]:
    """Return every scored file under ``dev/`` and ``holdout/``, corpus-relative."""
    found: list[str] = []
    for split in SPLITS:
        split_root = corpus / split
        if split_root.is_dir():
            found.extend(
                path.relative_to(corpus).as_posix()
                for path in split_root.rglob("*")
                if path.is_file() and path.suffix in CORPUS_SUFFIXES
            )
    return sorted(found)


def read_file_flag(*, entry: FileEntry, rule: str) -> bool:
    """A file-unit entry's label for *rule*: one flag, or one per rule when they differ."""
    flag = entry["flag"]
    return bool(flag[rule]) if isinstance(flag, dict) else bool(flag)


def build_expected(*, document: LabelsDocument, rule: str) -> dict[Site, bool]:
    """Map every labelled site to whether *rule* should flag it."""
    expected: dict[Site, bool] = {}
    for path, entry in document["files"].items():
        if "labels" in entry:
            for label in entry["labels"]:
                expected[(path, int(label["line"]))] = bool(label["flag"])
        else:
            expected[(path, 0)] = read_file_flag(entry=entry, rule=rule)
    return expected


def build_notes(*, document: LabelsDocument) -> dict[Site, str]:
    """Map every labelled site to its note, for the human report."""
    notes: dict[Site, str] = {}
    for path, entry in document["files"].items():
        if "labels" in entry:
            for label in entry["labels"]:
                notes[(path, int(label["line"]))] = label.get("note", "")
        else:
            notes[(path, 0)] = entry.get("note", "")
    return notes


def divide(*, numerator: float, denominator: float) -> float | None:
    """Return the ratio, or None when it is undefined (no denominator)."""
    return numerator / denominator if denominator else None


def measure(*, expected: dict[Site, bool], flagged: set[Site]) -> Metrics:
    """Count the confusion matrix of *flagged* against *expected*."""
    tp = sum(1 for site, want in expected.items() if want and site in flagged)
    fp = sum(1 for site, want in expected.items() if not want and site in flagged)
    fn = sum(1 for site, want in expected.items() if want and site not in flagged)
    tn = len(expected) - tp - fp - fn
    precision = divide(numerator=tp, denominator=tp + fp)
    recall = divide(numerator=tp, denominator=tp + fn)
    f1 = compute_f1(precision=precision, recall=recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def compute_f1(*, precision: float | None, recall: float | None) -> float | None:
    """Return the harmonic mean, 0.0 when both are zero, None when either is undefined."""
    if precision is None or recall is None:
        return None
    return divide(numerator=2 * precision * recall, denominator=precision + recall) or 0.0


def compute_gap(*, dev: Metrics, holdout: Metrics | None) -> Gap | None:
    """Return dev minus holdout per ratio; None when there is no holdout."""
    if holdout is None:
        return None
    gap: Gap = {"precision": None, "recall": None, "f1": None}
    for key in ("precision", "recall", "f1"):
        if dev[key] is not None and holdout[key] is not None:
            gap[key] = dev[key] - holdout[key]
    return gap


def select_sites(*, expected: dict[Site, bool], prefix: str) -> dict[Site, bool]:
    """Keep the labelled sites whose path starts with *prefix*."""
    return {site: want for site, want in expected.items() if site[0].startswith(prefix)}


def collect_flagged(
    *,
    corpus: Path,
    unit: str,
    find_flagged: Callable[[Path], set[Site]],
) -> set[Site]:
    """Run the rule over each split and return its sites, corpus-relative."""
    flagged: set[Site] = set()
    for split in SPLITS:
        split_root = corpus / split
        if not split_root.is_dir():
            continue
        for path, line in find_flagged(split_root):
            flagged.add((f"{split}/{path}", 0 if unit == "file" else line))
    return flagged


def evaluate_corpus(
    *,
    rule: str,
    corpus_name: str,
    find_flagged: Callable[[Path], set[Site]],
) -> Evaluation:
    """Score *rule* on its corpus, per split, and return the evaluation.

    *find_flagged* runs the rule over one split root and returns the flagged
    ``(path inside the split, line)`` pairs. Raises ValueError if the corpus has
    no labels file or the rule flags a site that ``labels.json`` does not cover,
    since a precision computed over unlabelled findings cannot be trusted.
    """
    corpus = CORPORA_ROOT / corpus_name
    if not (corpus / "labels.json").is_file():
        raise ValueError(f"labels file not found at {corpus / 'labels.json'}")
    document = read_labels(corpus=corpus)
    expected = build_expected(document=document, rule=rule)
    flagged = collect_flagged(corpus=corpus, unit=document["unit"], find_flagged=find_flagged)
    unlabelled = sorted(flagged - set(expected))
    if unlabelled:
        path, line = unlabelled[0]
        raise ValueError(
            f"{rule} flagged {len(unlabelled)} site(s) not in labels.json "
            f"(first: {corpus_name}/{path}:{line}); label them before scoring.",
        )
    record = build_record(rule=rule, corpus=corpus, document=document, flagged=flagged)
    return Evaluation(
        record=record,
        false_positives=sorted(
            site for site, want in expected.items() if not want and site in flagged
        ),
        false_negatives=sorted(
            site for site, want in expected.items() if want and site not in flagged
        ),
        notes=build_notes(document=document),
    )


def build_record(
    *,
    rule: str,
    corpus: Path,
    document: LabelsDocument,
    flagged: set[Site],
) -> ScoreRecord:
    """Assemble the combined, dev, holdout and generated metrics and the gap."""
    expected = build_expected(document=document, rule=rule)
    dev = measure(expected=select_sites(expected=expected, prefix="dev/"), flagged=flagged)
    held = select_sites(expected=expected, prefix="holdout/")
    holdout = measure(expected=held, flagged=flagged) if held else None
    record: ScoreRecord = {
        "rule": rule,
        "corpus": corpus.relative_to(REPO_ROOT).as_posix(),
        "split": "dev_and_holdout" if held else "dev_only",
        **measure(expected=expected, flagged=flagged),
        "dev": dev,
        "holdout": holdout,
    }
    generated = select_sites(expected=expected, prefix=GENERATED_PREFIX)
    if generated:
        record["holdout_generated"] = measure(expected=generated, flagged=flagged)
    record["gap"] = compute_gap(dev=dev, holdout=holdout)
    return record
