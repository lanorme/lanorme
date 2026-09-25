"""Tests for the SIMILAR-001 fuzzy near-duplicate check.

The headline test is a regression lock on the dev split of the labelled corpus
under ``evals/corpora/duplication_similar/`` (the holdout split is sealed and
scored by ``evals/score_similar.py``, never tuned against here). The rest run
the check on one pair per transform family: the edits that keep a pair a
near-duplicate (renamed identifiers, a renamed local callee, reordered
statements, an inserted statement) and the edits that make it two functions
(a flipped operator, a statement moved into a new branch, a changed callee,
changed string literals).
"""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.similarity import SimilarityCheck
from lanorme.scan import Scan

_DEV_SPLIT = (
    Path(__file__).resolve().parents[2] / "evals" / "corpora" / "duplication_similar" / "dev"
)

# One body per test, edited in one controlled way. ``combine`` is a parameter
# that is called, so a rename of it is a rename of data, not of an operation.
_BASE = """\
def {name}(xs, ys, combine):
    n = len(xs)
    if len(ys) != n:
        raise ValueError("lengths differ")
    if n < 2:
        raise ValueError("too few points")
    mean = fsum(xs) / n
    total = combine(xs, ys)
    return total / (n - 1)
"""
_FIRST = _BASE.format(name="first")


def _build_enabled_check() -> SimilarityCheck:
    check = SimilarityCheck()
    check.configure(settings={"enabled": True})
    return check


def _flags(tmp_path: Path, body: str) -> bool:
    # Score one case in isolation: write it alone, run the check, report whether
    # SIMILAR-001 fired (mirrors the corpus scoring methodology).
    path = tmp_path / "case.py"
    path.write_text(body, encoding="utf-8")
    result = _build_enabled_check().check(Scan(root=tmp_path))
    return any(w.rule == "SIMILAR-001" for w in result.warnings)


def _pair(second: str) -> str:
    """The base function followed by *second*, its edited copy."""
    return _FIRST + "\n\n" + second


def test_dev_split_precision_is_perfect_and_recall_is_high():
    # Arrange: each dev file scored in isolation against its directory label.
    check = _build_enabled_check()
    tp = fp = fn = tn = 0
    for label, folder in (("pos", "positives"), ("neg", "negatives")):
        cases = sorted((_DEV_SPLIT / folder).glob("*.py"))
        assert cases, f"no {folder} under the dev split"
        for case in cases:
            flagged = any(
                w.rule == "SIMILAR-001"
                for w in check.check(Scan(root=case.parent)).warnings
                if w.file == case.name
            )
            if label == "pos":
                tp += flagged
                fn += not flagged
            else:
                fp += flagged
                tn += not flagged

    # Act.
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0

    # Assert: precision-first contract, locked as a regression.
    assert precision == 1.0, f"precision regressed to {precision:.3f} (FP={fp})"
    assert recall >= 0.80, f"recall regressed to {recall:.3f} (FN={fn})"


def test_renamed_identifiers_and_local_callee_are_flagged(tmp_path: Path):
    # Arrange: every parameter and local renamed, the called parameter included.
    second = (
        "def second(alpha, beta, merge):\n"
        "    count = len(alpha)\n"
        "    if len(beta) != count:\n"
        '        raise ValueError("lengths differ")\n'
        "    if count < 2:\n"
        '        raise ValueError("too few points")\n'
        "    centre = fsum(alpha) / count\n"
        "    joined = merge(alpha, beta)\n"
        "    return joined / (count - 1)\n"
    )

    # Act + Assert: names are data, a call through a parameter included.
    assert _flags(tmp_path, _pair(second))


def test_reordered_independent_statements_are_flagged(tmp_path: Path):
    # Arrange: the two independent assignments swapped.
    second = _BASE.format(name="second").replace(
        "    mean = fsum(xs) / n\n    total = combine(xs, ys)\n",
        "    total = combine(xs, ys)\n    mean = fsum(xs) / n\n",
    )

    # Act + Assert: order of independent statements is drift, not a change.
    assert _flags(tmp_path, _pair(second))


def test_inserted_guard_statement_is_flagged(tmp_path: Path):
    # Arrange: one early-return guard added at the top of the copy.
    second = _BASE.format(name="second").replace(
        "    n = len(xs)\n",
        "    if not xs:\n        return None\n    n = len(xs)\n",
    )

    # Act + Assert: an inserted statement is drift.
    assert _flags(tmp_path, _pair(second))


def test_inserted_statement_with_a_new_string_is_flagged(tmp_path: Path):
    # Arrange: one added call carrying two strings the original does not have
    # (a Jaccard over strings would fall to 0.5; containment stays at 1.0).
    second = _BASE.format(name="second").replace(
        "    mean = fsum(xs) / n\n",
        '    record("samples", "count", n)\n    mean = fsum(xs) / n\n',
    )

    # Act + Assert: the strings of the smaller side all appear in the other.
    assert _flags(tmp_path, _pair(second))


def test_flipped_operator_is_not_flagged(tmp_path: Path):
    # Arrange: one comparison turned to its opposite.
    second = _BASE.format(name="second").replace("if n < 2:", "if n > 2:")

    # Act + Assert: a changed operator is a changed operation.
    assert not _flags(tmp_path, _pair(second))


def test_statement_moved_into_a_new_branch_is_not_flagged(tmp_path: Path):
    # Arrange: an assignment wrapped in a branch the original never had.
    second = _BASE.format(name="second").replace(
        "    mean = fsum(xs) / n\n",
        "    if xs:\n        mean = fsum(xs) / n\n    else:\n        mean = None\n",
    )

    # Act + Assert: the statement's control-flow position changed.
    assert not _flags(tmp_path, _pair(second))


def test_changed_callee_is_not_flagged(tmp_path: Path):
    # Arrange: one call to a builtin swapped for another.
    second = _BASE.format(name="second").replace("n = len(xs)", "n = sum(xs)")

    # Act + Assert: a changed called name is a changed operation.
    assert not _flags(tmp_path, _pair(second))


def test_changed_callee_inside_a_conditional_expression_is_not_flagged(tmp_path: Path):
    # Arrange: the callee changes inside an ``x if c else y`` expression.
    first = _FIRST.replace("    return total / (n - 1)\n", "    return len(xs) if n else total\n")
    second = _BASE.format(name="second").replace(
        "    return total / (n - 1)\n",
        "    return sum(xs) if n else total\n",
    )

    # Act + Assert: a conditional expression is part of its statement.
    assert not _flags(tmp_path, first + "\n\n" + second)


def test_calls_replaced_by_subscripts_are_not_flagged(tmp_path: Path):
    # Arrange: two statements keep their shape but call nothing any more.
    second = (
        _BASE.format(name="second")
        .replace("    n = len(xs)\n", "    n = xs[0]\n")
        .replace("    mean = fsum(xs) / n\n", "    mean = xs[1] / n\n")
    )

    # Act + Assert: a call is an operation of its statement.
    assert not _flags(tmp_path, _pair(second))


def test_changed_string_literals_are_not_flagged(tmp_path: Path):
    # Arrange: every string literal rewritten.
    second = (
        _BASE.format(name="second")
        .replace('"lengths differ"', '"alder"')
        .replace('"too few points"', '"birch"')
    )

    # Act + Assert: strings are the content a body is about.
    assert not _flags(tmp_path, _pair(second))


def test_parallel_builders_sharing_a_shape_but_not_keys_are_not_flagged(tmp_path: Path):
    # Arrange: the same straight-line shape writing different keys.
    body = (
        "def database(host, port, name):\n"
        "    config = {}\n"
        '    config["driver"] = "postgresql"\n'
        '    config["host"] = host\n'
        '    config["port"] = port\n'
        '    config["database"] = name\n'
        "    return config\n\n"
        "def cache(host, port, name):\n"
        "    config = {}\n"
        '    config["driver"] = "redis"\n'
        '    config["host"] = host\n'
        '    config["ttl"] = port\n'
        '    config["namespace"] = name\n'
        "    return config\n"
    )

    # Act + Assert: boilerplate, not a clone.
    assert not _flags(tmp_path, body)


def test_lowered_operation_threshold_admits_a_flipped_operator(tmp_path: Path):
    # Arrange: the flipped pair, and a check told to tolerate one changed operation.
    second = _BASE.format(name="second").replace("if n < 2:", "if n > 2:")
    (tmp_path / "case.py").write_text(_pair(second), encoding="utf-8")
    check = SimilarityCheck()
    check.configure(settings={"enabled": True, "op_jaccard": 0.8})

    # Act.
    result = check.check(Scan(root=tmp_path))

    # Assert: the gate reads its configured threshold.
    assert any(w.rule == "SIMILAR-001" for w in result.warnings)


def test_attribute_renamed_clone_is_flagged(tmp_path: Path):
    # Arrange + Act: same computation, one attribute renamed (DRY-001 misses this).
    body = (
        "def a(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n\n"
        "def b(s):\n x = s.gamma\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n"
    )

    # Assert.
    assert _flags(tmp_path, body)


def test_parallel_builder_with_disjoint_attrs_is_not_flagged(tmp_path: Path):
    # Arrange + Act: same dict keys, disjoint source attributes (precision trap).
    body = (
        "def a(o):\n d = {}\n d['host'] = o.mail_host\n d['port'] = o.mail_port\n"
        " d['user'] = o.mail_user\n return d\n\n"
        "def b(o):\n d = {}\n d['host'] = o.bucket_host\n d['port'] = o.bucket_port\n"
        " d['user'] = o.bucket_user\n return d\n"
    )

    # Assert.
    assert not _flags(tmp_path, body)


def test_disabled_by_default(tmp_path: Path):
    # Arrange: a clear clone pair.
    body = (
        "def a(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n\n"
        "def b(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n"
    )
    (tmp_path / "m.py").write_text(body, encoding="utf-8")

    # Act: the default check ships off.
    result = SimilarityCheck().check(Scan(root=tmp_path))

    # Assert.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_findings_are_warnings_not_violations(tmp_path: Path):
    # Arrange + Act.
    body = (
        "def a(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n\n"
        "def b(s):\n x = s.gamma\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n"
    )
    (tmp_path / "m.py").write_text(body, encoding="utf-8")
    result = _build_enabled_check().check(Scan(root=tmp_path))

    # Assert: advisory only, never fails the build.
    assert result.status == Status.WARN
    assert result.violations == []
    assert result.warnings


def test_root_under_a_skip_named_ancestor_is_still_scanned(tmp_path: Path):
    # Arrange: a clone pair in a project checked out under a migrations/ dir.
    root = tmp_path / "migrations" / "project"
    root.mkdir(parents=True)
    body = (
        "def a(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n\n"
        "def b(s):\n x = s.gamma\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n"
    )

    # Act: scan the project, not its ancestor.
    flagged = _flags(root, body)

    # Assert: the ancestor is the user's filesystem, not the project layout.
    assert flagged
