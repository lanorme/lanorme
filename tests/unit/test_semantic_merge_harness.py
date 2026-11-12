"""Tests for the semantic merge spike's harness: the numbers its results rest on.

The spike in ``experiments/semantic-merge/`` reports separations, agreements
and clusterings. These tests pin the pieces a wrong implementation would
silently skew: the AUC and its cluster bootstrap, Cohen's kappa, the
Modularization Quality search and its bookkeeping, the adjusted Rand index,
the co-change filters, the vocabulary terms and the attribution of an import
through a package's ``__init__.py``.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parents[2] / "experiments" / "semantic-merge" / "harness"
sys.path.insert(0, str(_HARNESS))

import cochange_signals  # noqa: E402 -- the harness directory is not a package
import labelled_packages  # noqa: E402
import modularization_quality  # noqa: E402
import report_ratings  # noqa: E402
import reuse_signals  # noqa: E402
import separation  # noqa: E402
import vocabulary_signals  # noqa: E402

TWO_TRIANGLES = {
    ("a", "b"),
    ("b", "c"),
    ("a", "c"),
    ("d", "e"),
    ("e", "f"),
    ("d", "f"),
    ("c", "d"),
}


def write_tree(root: Path, files: dict[str, str]) -> None:
    """Write *files* under *root*."""
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def build_view(root: Path, *, package: str) -> reuse_signals.PackageView:
    """The reuse view of *package* in the tree at *root*."""
    original = labelled_packages.read_original_tree(root)
    modules = {
        module.relative: module for found in original.tree.by_directory.values() for module in found
    }
    return reuse_signals.PackageView(
        package=package,
        members=tuple(original.collect_members(package)),
        modules=modules,
        graph=original.tree.graph,
    )


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ([3.0, 4.0], [1.0, 2.0], 1.0),
        ([1.0, 2.0], [3.0, 4.0], 0.0),
        ([1.0, 1.0], [1.0], 0.5),
        ([2.0], [1.0, 3.0], 0.5),
    ],
)
def test_auc_is_the_share_of_pairs_the_first_group_wins(first, second, expected):
    # Arrange
    groups = {"first": first, "second": second}

    # Act
    score = separation.compute_auc(**groups)

    # Assert
    assert score == expected


def test_auc_is_undefined_without_both_groups():
    # Arrange
    values = [1.0, 2.0]

    # Act
    score = separation.compute_auc(first=values, second=[])

    # Assert
    assert score is None


def test_interval_resamples_lineages_and_covers_a_perfect_separation():
    # Arrange
    observations = [
        separation.Observation(value=float(index), is_first=index >= 4, lineage=f"repo{index}")
        for index in range(8)
    ]

    # Act
    interval = separation.compute_interval(observations)

    # Assert
    assert interval == (1.0, 1.0)


def test_interval_needs_two_lineages():
    # Arrange
    observations = [
        separation.Observation(value=1.0, is_first=True, lineage="one"),
        separation.Observation(value=0.0, is_first=False, lineage="one"),
    ]

    # Act
    interval = separation.compute_interval(observations)

    # Assert
    assert interval is None


@pytest.mark.parametrize(
    ("pairs", "kappa"),
    [
        ([(True, True), (False, False)], 1.0),
        ([(True, True), (True, False), (False, True), (False, False)], 0.0),
        ([(True, False), (False, True)], -1.0),
    ],
)
def test_kappa_corrects_agreement_for_chance(pairs, kappa):
    # Arrange
    ratings = list(pairs)

    # Act
    found = report_ratings.compute_kappa(ratings)

    # Assert
    assert found["kappa"] == pytest.approx(kappa)


@pytest.mark.parametrize(
    ("labels", "level"),
    [
        ({"a": "merge", "b": "keep"}, "merge"),
        ({"a": "keep", "b": "keep"}, "keep"),
        ({"a": "contested", "b": "keep"}, "contested"),
        ({"a": "keep"}, "keep"),
    ],
)
def test_ratings_combine_to_merge_if_any_and_keep_if_all(labels, level):
    # Arrange
    ratings = dict(labels)

    # Act
    combined = report_ratings.combine_ratings(ratings)

    # Assert
    assert combined == level


def test_search_finds_the_two_triangles():
    # Arrange
    neighbours = modularization_quality.build_neighbours(nodes=list("abcdef"), edges=TWO_TRIANGLES)

    # Act
    best = modularization_quality.search(neighbours=neighbours, restarts=10, seed=1)[0]

    # Assert
    assert len({best[node] for node in "abc"}) == 1
    assert len({best[node] for node in "def"}) == 1
    assert best["a"] != best["d"]
    quality = modularization_quality.measure_quality(neighbours=neighbours, cluster_of=best)
    assert quality == pytest.approx(2 * 6 / 7)


def test_moving_a_node_keeps_the_edge_counts_equal_to_a_recount():
    # Arrange
    neighbours = modularization_quality.build_neighbours(nodes=list("abcdef"), edges=TWO_TRIANGLES)
    generator = random.Random(3)
    partition = modularization_quality.Partition(
        neighbours=neighbours,
        cluster_of={node: generator.randrange(3) for node in "abcdef"},
    )

    # Act
    for _step in range(100):
        partition.move(node=generator.choice("abcdef"), target=generator.randrange(4))

    # Assert
    recounted = modularization_quality.measure_quality(
        neighbours=neighbours,
        cluster_of=partition.cluster_of,
    )
    assert partition.compute_quality() == pytest.approx(recounted)


def test_adjusted_rand_is_one_for_identical_and_zero_for_one_cluster():
    # Arrange
    truth = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1}
    renamed = {node: 5 - cluster for node, cluster in truth.items()}
    lumped = dict.fromkeys(truth, 0)

    # Act
    same = modularization_quality.compute_adjusted_rand(first=truth, second=renamed)
    none = modularization_quality.compute_adjusted_rand(first=truth, second=lumped)

    # Assert
    assert same == 1.0
    assert none == 0.0


def test_history_drops_additions_and_large_commits():
    # Arrange
    large = "".join(f"M\tbulk/file{index}.py\n" for index in range(31))
    log = (
        "\x00c3\nM\tpkg/a.py\nM\tpkg/b.py\n"
        "\x00c2\nA\tpkg/b.py\nM\tpkg/a.py\n"
        f"\x00c1\nM\tpkg/a.py\n{large}"
        "\x00c0\nA\tpkg/a.py\n"
    )

    # Act
    changes = cochange_signals._parse_log(log)

    # Assert
    assert changes == {"pkg/a.py": ["c3", "c2"], "pkg/b.py": ["c3"]}


def test_cochange_is_undefined_below_three_commits_and_measured_at_three():
    # Arrange
    members = ["pkg/a.py", "pkg/b.py"]
    short = cochange_signals.History(changes={"pkg/a.py": frozenset({"c1", "c2"})})
    enough = cochange_signals.History(
        changes={"pkg/a.py": frozenset({"c1", "c2", "c3"}), "pkg/b.py": frozenset({"c3"})},
    )

    # Act
    undefined = cochange_signals.measure_cochange(history=short, members=members)
    measured = cochange_signals.measure_cochange(history=enough, members=members)
    absent = cochange_signals.measure_cochange(history=None, members=members)

    # Assert
    assert undefined["changed_together"] is None
    assert undefined["commit_count"] == 2
    assert measured["changed_together"] == pytest.approx(1 / 3)
    assert measured["cochange_similarity"] == pytest.approx(1 / 3)
    assert absent["commit_count"] is None


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("HTTPServer_id2", ["http", "server", "id"]),
        ("redact_pii", ["redact", "pii"]),
        ("SessionKey", ["session", "key"]),
        ("x", []),
    ],
)
def test_identifiers_split_on_underscores_and_capitals(name, words):
    # Arrange
    identifier = name

    # Act
    found = vocabulary_signals.split_identifier(identifier)

    # Assert
    assert found == words


def test_vocabulary_scores_shared_terms_above_unrelated_ones(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/invoice.py": "def total_invoice(invoice_lines):\n    return sum(invoice_lines)\n",
            "app/billing.py": "def send_invoice(invoice_lines):\n    return invoice_lines\n",
            "app/weather.py": "def read_forecast(station):\n    return station\n",
            "app/clock.py": "def read_time(zone):\n    return zone\n",
        },
    )
    original = labelled_packages.read_original_tree(tmp_path)
    modules = [module for found in original.tree.by_directory.values() for module in found]

    # Act
    vocabulary = vocabulary_signals.build_vocabulary(modules=modules, with_prose=False)

    # Assert
    related = vocabulary.measure_cosine(first="app/invoice.py", second="app/billing.py")
    unrelated = vocabulary.measure_cosine(first="app/invoice.py", second="app/weather.py")
    assert related > unrelated
    assert unrelated == 0.0


def test_an_import_of_the_package_is_attributed_to_the_reexported_member(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/schemas/__init__.py": "from .user import User\nfrom .team import Team\n",
            "app/schemas/user.py": "class User:\n    name = 'user'\n",
            "app/schemas/team.py": "class Team:\n    name = 'team'\n",
            "app/routes.py": "from app.schemas import User\n",
            "app/admin_view.py": "from app.schemas.user import User\nfrom app.schemas.team import Team\n",
        },
    )

    # Act
    uses = reuse_signals.collect_uses(view=build_view(tmp_path, package="app/schemas"))

    # Assert
    assert uses == {
        "app/routes.py": {"app/schemas/user.py"},
        "app/admin_view.py": {"app/schemas/user.py", "app/schemas/team.py"},
    }


def test_reuse_signals_count_importers_that_use_two_members_and_skip_tests(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/rules/__init__.py": "",
            "app/rules/limits.py": "LIMIT = 3\n",
            "app/rules/names.py": "from app.rules.limits import LIMIT\nNAME = 'n' * LIMIT\n",
            "app/service.py": "from app.rules.limits import LIMIT\nfrom app.rules.names import NAME\n",
            "app/report.py": "from app.rules.names import NAME\n",
            "tests/test_rules.py": "from app.rules.limits import LIMIT\n",
        },
    )
    view = build_view(tmp_path, package="app/rules")

    # Act
    signals = reuse_signals.measure_reuse(view=view)
    sentence = reuse_signals.describe_reuse(view=view)

    # Assert
    assert signals["importer_count"] == 2
    assert signals["used_together"] == 0.5
    assert signals["mean_share_used"] == 0.75
    assert signals["internal_density"] == 1.0
    assert signals["private_share"] == 0.0
    assert sentence == (
        "2 modules import them, 1 of those importing two or more, and some import one another."
    )


def test_a_package_nothing_outside_imports_has_no_reuse_evidence(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/rules/__init__.py": "",
            "app/rules/limits.py": "LIMIT = 3\n",
            "app/rules/names.py": "NAME = 'n'\n",
        },
    )
    view = build_view(tmp_path, package="app/rules")

    # Act
    signals = reuse_signals.measure_reuse(view=view)

    # Assert
    assert signals["used_together"] is None
    assert signals["importer_similarity"] is None
    assert signals["private_share"] == 1.0
    assert reuse_signals.describe_reuse(view=view).startswith("No production module outside")
