"""Warning baseline: record the findings a codebase already has so that only
new ones report.

On a brownfield codebase the advisory tier decays into wallpaper: the same
warnings ride every run, nobody acts on them, and a genuinely new warning
scrolls past unnoticed in the pile. A baseline records the existing debt and
holds the project to account only for what it adds. With ``extends = ["strict"]``
plus a baseline, every new line is held to strict from day one of a legacy repo.

The matching is content-anchored, never line-number-anchored, so an entry
survives unrelated edits above it. A finding is keyed by
``(file, rule code, anchor)`` where the anchor is a hash of the stripped source
line at the finding, or (for a file-level finding reported at a line-1 sentinel)
the fixed marker ``file``, since the file and code already identify it. Hashing
the source form keeps source text and any secret out of the committed file.

A baselined *warning* never suppresses a current *error*-tier finding, so a
baselined file that grows past a hard threshold re-reports and fails the build.
A per-key count budget guarantees that N recorded occurrences can never hide an
(N+1)th: new debt always surfaces.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from lanorme import CheckResult, Violation, extract_code
from lanorme.errors import UsageError
from lanorme.source_lines import SourceLines

BASELINE_VERSION = 1

_ERROR = "error"
_WARNING = "warning"
# The anchor of a finding that is about the whole file rather than a line.
_FILE_ANCHOR = "file"

# A finding's identity in the baseline: ``(path, code, anchor)``.
Key = tuple[str, str, str]


# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #


def _normalise_path(file: str) -> str:
    """Normalise a finding path to forward slashes without a leading ``./``."""
    normalised = file.replace("\\", "/")
    return normalised[2:] if normalised.startswith("./") else normalised


def _describe(rule: str) -> str:
    """The static rule description, with no per-finding (and so no secret) text.

    ``Violation.rule`` is a fixed string such as
    ``"SIZE-001: File approaching the effective line limit"``; the dynamic detail
    lives in ``Violation.message``. Storing the description keeps the committed
    file readable for review without ever recording a value or a source snippet.
    """
    head, sep, tail = rule.partition(":")
    return tail.strip() if sep else head.strip()


class FindingKeys:
    """The baseline identity of each finding, memoised over one run's source lines.

    The suppression, the drift note, ``baseline status`` and the fingerprint in
    the JSON reports all key findings the same way; sharing one instance per
    run means each source line is read once and each key computed once.
    """

    def __init__(self, lines: SourceLines) -> None:
        self.lines = lines
        self._memo: dict[tuple[str, str, int], Key] = {}

    def build_key(self, finding: Violation) -> Key:
        """The ``(path, code, anchor)`` identity used to match against the baseline."""
        memo_key = (finding.file, finding.rule, finding.line)
        key = self._memo.get(memo_key)
        if key is None:
            key = (
                _normalise_path(finding.file),
                extract_code(finding.rule),
                self._anchor(file=finding.file, line=finding.line),
            )
            self._memo[memo_key] = key
        return key

    def compute_fingerprint(self, finding: Violation) -> str:
        """A short stable identity for a finding, the baseline's key hashed.

        It survives edits elsewhere in the file (the anchor is the finding's own
        line) and is what a tool should key on to tell a fixed finding from a
        moved one. Empty for a finding that belongs to no file.
        """
        if not finding.file:
            return ""
        return hashlib.sha256("|".join(self.build_key(finding)).encode("utf-8")).hexdigest()[:16]

    def _anchor(self, *, file: str, line: int) -> str:
        """A stable, content-derived key for a finding.

        For a line-anchored finding (line >= 2) the anchor is a hash of the
        stripped source line, so it survives unrelated edits above the finding.
        A file-level finding is reported at a line-1 sentinel (SIZE-001,
        PORT-001, TESTFILE-001 and friends) and is about the whole file, not
        line 1; anchoring it to the text on line 1, or to its count-bearing
        message, would resurrect it on an unrelated top-of-file edit or a minor
        metric change. So file-level findings (line <= 1) and findings whose
        source line is blank/unreadable take the fixed :data:`_FILE_ANCHOR`:
        the key's file and code already identify them, and unlike the rule
        description the marker does not change with the tier (``exceeds``
        against ``approaching``), so a recorded error still covers the warning
        it improves into. The hashed form carries no source text or secret.
        """
        if line >= 2:
            source = self.lines.read_line(file=file, line=line).strip()
            if source:
                return "sha:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
        return _FILE_ANCHOR


def _iter_findings(results: list[CheckResult]) -> list[tuple[Violation, str]]:
    """Every file-bearing finding with its tier; a RUN-000 crash notice has no file."""
    return [
        (finding, tier)
        for result in results
        for tier, findings in ((_ERROR, result.violations), (_WARNING, result.warnings))
        for finding in findings
        if finding.file
    ]


# --------------------------------------------------------------------------- #
# The recorded baseline
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Entry:
    """What the baseline holds for one key: the worst tier seen and how many."""

    severity: str
    count: int

    def merge(self, *, severity: str, count: int) -> _Entry:
        """This entry with *count* more occurrences, raised to ``error`` if any is one."""
        worst = _ERROR if _ERROR in (self.severity, severity) else _WARNING
        return _Entry(severity=worst, count=self.count + count)


def _fail(message: str) -> None:
    """Refuse the baseline file with a usage error (exit 2 at the CLI)."""
    raise UsageError(message)


def _read_entries(path: Path) -> list[object]:
    """The raw ``entries`` list of a baseline file, refusing a missing or malformed one."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _fail(f"baseline file '{path}' does not exist. Run 'lanorme baseline write' first.")
    except OSError as error:
        _fail(f"baseline file '{path}' could not be read: {error}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        _fail(f"baseline file '{path}' is not valid JSON: {error}")

    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        _fail(f"baseline file '{path}' is not a version {BASELINE_VERSION} baseline.")
    entries = data.get("entries")
    if not isinstance(entries, list):
        _fail(f"baseline file '{path}' has no 'entries' list.")
    return entries


def _parse_entry(*, path: Path, entry: object) -> tuple[Key, _Entry]:
    """One recorded entry as its key and budget, refusing a malformed one."""
    try:
        key = (str(entry["file"]), str(entry["code"]), str(entry["anchor"]))
        count = int(entry.get("count", 1))
    except (KeyError, TypeError, ValueError, AttributeError):
        _fail(f"baseline file '{path}' has a malformed entry: {entry!r}")
    severity = _ERROR if entry.get("severity") == _ERROR else _WARNING
    return key, _Entry(severity=severity, count=count)


class Baseline:
    """The recorded findings of a project, matched against a run's findings.

    Load it with :meth:`load`; every method takes the run's results as the
    checks produced them (before any baseline filtering) and keys them through
    the shared :class:`FindingKeys`.
    """

    def __init__(self, *, entries: dict[Key, _Entry], keys: FindingKeys) -> None:
        self.entries = entries
        self.keys = keys

    @classmethod
    def load(cls, path: Path, *, keys: FindingKeys) -> Baseline:
        """Read a baseline file, raising :class:`UsageError` when it is missing or malformed."""
        entries: dict[Key, _Entry] = {}
        for raw in _read_entries(path):
            key, entry = _parse_entry(path=path, entry=raw)
            existing = entries.get(key)
            entries[key] = (
                entry
                if existing is None
                else existing.merge(
                    severity=entry.severity,
                    count=entry.count,
                )
            )
        return cls(entries=entries, keys=keys)

    def build_key(self, finding: Violation) -> Key:
        """The ``(path, code, anchor)`` identity of *finding*."""
        return self.keys.build_key(finding)

    def suppress(self, results: list[CheckResult]) -> list[CheckResult]:
        """*results* with baselined findings removed and statuses recomputed.

        A recorded warning never hides a current error-tier finding (a
        baselined file that crossed a hard threshold must re-report), while a
        recorded error does cover its improved warning. Each key's count is a
        budget: N recorded occurrences never hide an (N+1)th.
        """
        consumed: dict[Key, int] = {}

        def is_kept(*, finding: Violation, tier: str) -> bool:
            if not finding.file:
                return True
            key = self.build_key(finding)
            entry = self.entries.get(key)
            if entry is None or (tier == _ERROR and entry.severity != _ERROR):
                return True
            used = consumed.get(key, 0)
            if used >= entry.count:
                return True
            consumed[key] = used + 1
            return False

        return [
            CheckResult.from_findings(
                check=result.check,
                violations=[v for v in result.violations if is_kept(finding=v, tier=_ERROR)],
                warnings=[w for w in result.warnings if is_kept(finding=w, tier=_WARNING)],
            )
            for result in results
        ]

    def find_drift(self, results: list[CheckResult]) -> list[tuple[str, str]]:
        """``(file, code)`` pairs whose baseline entry stopped matching its finding.

        Drift is the conjunction of two facts about one file and rule: the
        baseline holds an entry that matched nothing this run, and a finding of
        that same file and rule did not match the baseline either. Together
        they say the entry's anchor moved, so the finding is recorded debt the
        baseline no longer recognises rather than something new. An upgrade
        that rewords a rule description does exactly this.

        Requiring the entry to be stale is what keeps genuinely new debt out: a
        file whose recorded entries all still match reports nothing here, even
        when a further finding of the same rule appears alongside them.
        """
        if not self.entries:
            return []
        keys = [self.build_key(finding) for finding, _tier in _iter_findings(results)]
        unmatched = {(key[0], key[1]) for key in keys if key not in self.entries}
        stale = {(file, code) for file, code, _anchor in self.find_stale(results)}
        return sorted(stale & unmatched)

    def find_stale(self, results: list[CheckResult]) -> list[Key]:
        """The recorded keys no finding in *results* matches, sorted."""
        matched = {self.build_key(finding) for finding, _tier in _iter_findings(results)}
        return sorted(key for key in self.entries if key not in matched)


# --------------------------------------------------------------------------- #
# Commands: write and status
# --------------------------------------------------------------------------- #


def _build_entries(*, results: list[CheckResult], keys: FindingKeys) -> list[dict[str, object]]:
    """The serialisable entry list for the current findings of a clean run.

    A finding with no file is a RUN-000 crash notice: transient and
    version-dependent, so it is never recorded.
    """
    tally: dict[Key, _Entry] = {}
    described: dict[Key, str] = {}
    for finding, tier in _iter_findings(results):
        key = keys.build_key(finding)
        existing = tally.get(key)
        if existing is None:
            tally[key] = _Entry(severity=tier, count=1)
            described[key] = _describe(finding.rule)
        else:
            tally[key] = existing.merge(severity=tier, count=1)
    return [
        {
            "file": key[0],
            "code": key[1],
            "anchor": key[2],
            "severity": entry.severity,
            "message": described[key],
            "count": entry.count,
        }
        for key, entry in tally.items()
    ]


def _serialise(entries: list[dict[str, object]]) -> str:
    """Render entries as deterministic, diff-friendly JSON with a trailing newline."""
    ordered = sorted(entries, key=lambda e: (e["file"], e["code"], e["anchor"]))
    payload = {"version": BASELINE_VERSION, "entries": ordered}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write(
    *,
    results: list[CheckResult],
    project_root: Path,
    baseline_path: Path,
    keys: FindingKeys | None = None,
) -> None:
    """Record the current findings, printing a paydown summary (and, on the first
    write, the config block to adopt).

    *keys* is the run's shared :class:`FindingKeys`; a fresh one over
    *project_root* is used when it is not given.
    """
    keys = keys or FindingKeys(SourceLines(project_root))
    first_write = not baseline_path.exists()
    old_keys = set() if first_write else set(Baseline.load(baseline_path, keys=keys).entries)

    entries = _build_entries(results=results, keys=keys)
    new_keys = {(e["file"], e["code"], e["anchor"]) for e in entries}
    added = len(new_keys - old_keys)
    pruned = len(old_keys - new_keys)

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(_serialise(entries), encoding="utf-8")

    total = sum(e["count"] for e in entries)
    entry_word = "entry" if len(entries) == 1 else "entries"
    finding_word = "finding" if total == 1 else "findings"
    print(
        f"Wrote {len(entries)} baseline {entry_word} ({total} {finding_word}): "
        f"+{added} new, -{pruned} pruned (was {len(old_keys)}).",
    )
    if first_write:
        print(
            "\nAdd this to your configuration and commit the file like a lockfile:\n\n"
            "    [tool.lanorme]\n"
            f'    baseline = "{_display_path(baseline_path=baseline_path, project_root=project_root)}"\n',
        )


def _display_path(*, baseline_path: Path, project_root: Path) -> str:
    """The baseline path as written in config (project-relative where possible)."""
    try:
        return baseline_path.relative_to(project_root).as_posix()
    except ValueError:
        return baseline_path.name


def print_status(
    *,
    results: list[CheckResult],
    project_root: Path,
    baseline_path: Path,
    keys: FindingKeys | None = None,
) -> None:
    """List baseline entries that match nothing in the current run (stale debt)."""
    recorded = Baseline.load(baseline_path, keys=keys or FindingKeys(SourceLines(project_root)))
    stale = recorded.find_stale(results)
    if not stale:
        print(f"Baseline is current: all {len(recorded.entries)} entries still match a finding.")
        return
    print(
        f"{len(stale)} stale baseline {'entry' if len(stale) == 1 else 'entries'} (matched nothing this run):",
    )
    grouped: dict[tuple[str, str], int] = {}
    for file, code, _anchor_hash in stale:
        grouped[(file, code)] = grouped.get((file, code), 0) + 1
    for (file, code), count in grouped.items():
        suffix = f"  (x{count})" if count > 1 else ""
        print(f"  {file}  {code}{suffix}")
    print("\nRun 'lanorme baseline write' to prune them.")
