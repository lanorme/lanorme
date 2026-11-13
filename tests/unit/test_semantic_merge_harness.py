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

import boundary_signals  # noqa: E402 -- the harness directory is not a package
import cochange_signals  # noqa: E402
import definition_pairs  # noqa: E402
import idle_boundaries  # noqa: E402
import labelled_packages  # noqa: E402
import logistic  # noqa: E402
import measure_colocation  # noqa: E402
import measure_clustering  # noqa: E402
import measure_congruence  # noqa: E402
import member_surfaces  # noqa: E402
import modularization_quality  # noqa: E402
import report_confirmation  # noqa: E402
import report_exploration  # noqa: E402
import report_package_scores  # noqa: E402
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


def test_history_follows_a_file_through_exact_renames_even_in_a_large_commit():
    # Arrange
    moved = "".join(f"R100\told/file{index}.py\tnew/file{index}.py\n" for index in range(31))
    log = (
        "\x00c4\nM\tpkg/a.py\n"
        "\x00c3\nR100\tlegacy/a.py\tpkg/a.py\n"
        "\x00c2\nM\tlegacy/a.py\n"
        f"\x00c1\nR100\tfirst/a.py\tlegacy/a.py\n{moved}"
        "\x00c0\nM\tfirst/a.py\nM\told/file0.py\n"
    )

    # Act
    changes = cochange_signals._parse_log(log)

    # Assert
    assert changes == {"pkg/a.py": ["c4", "c2", "c0"], "new/file0.py": ["c0"]}


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


def test_running_a_package_file_on_the_way_to_a_submodule_is_not_a_use_of_it(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/rules/__init__.py": "DEFAULT = 1\n\ndef read_default():\n    return DEFAULT\n",
            "app/rules/limits.py": "LIMIT = 3\n",
            "app/rules/names.py": "NAME = 'n'\n",
            "app/service.py": "from app.rules.limits import LIMIT\n",
            "app/report.py": "from app.rules import read_default\n",
            "app/loader.py": "from app.rules import names\n",
        },
    )

    # Act
    uses = reuse_signals.collect_uses(view=build_view(tmp_path, package="app/rules"))

    # Assert
    assert uses == {
        "app/service.py": {"app/rules/limits.py"},
        "app/report.py": {"app/rules/__init__.py"},
        "app/loader.py": {"app/rules/names.py"},
    }


def test_clustering_graph_drops_isolated_modules_and_migrations(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/service.py": "from app.store import save\n",
            "app/store.py": "def save():\n    return 1\n",
            "app/alone.py": "VALUE = 1\n",
            "app/migrations/__init__.py": "",
            "app/migrations/first.py": "from app.store import save\n",
        },
    )

    # Act
    graph = measure_clustering.build_graph(labelled_packages.read_original_tree(tmp_path))

    # Assert
    assert sorted(graph) == ["app/service.py", "app/store.py"]


def test_congruence_pairs_are_capped_per_package_and_skip_migrations(tmp_path: Path):
    # Arrange
    files = {"app/__init__.py": "", "app/big/__init__.py": "", "app/small/__init__.py": ""}
    files.update({f"app/big/module{index}.py": "VALUE = 1\n" for index in range(12)})
    files.update({f"app/small/module{index}.py": "VALUE = 1\n" for index in range(2)})
    files.update({f"app/migrations/step{index}.py": "VALUE = 1\n" for index in range(5)})
    write_tree(tmp_path, files)
    original = labelled_packages.read_original_tree(tmp_path)
    modules = {
        module.relative: module for found in original.tree.by_directory.values() for module in found
    }

    # Act
    same, across = measure_congruence.sample_pairs(modules)

    # Assert
    in_big = [pair for pair in same if pair[0].startswith("app/big/")]
    assert len(in_big) == measure_congruence.MAX_PAIRS_PER_PACKAGE
    assert len(same) == measure_congruence.MAX_PAIRS_PER_PACKAGE + 1
    assert len(across) == 24
    assert not any("migrations" in name for pair in (*same, *across) for name in pair)


def read_package_boundaries(root: Path, *, package: str) -> boundary_signals.Boundaries:
    """The boundaries of *package* in the tree at *root*."""
    view = build_view(root, package=package)
    surfaces = [member_surfaces.read_surface(member) for member in view.members]
    return boundary_signals.read_boundaries(view=view, surfaces=surfaces)


DECLARATIONS = (
    "from typing import Protocol\n\n\n"
    "LIMIT = 3\n\n\n"
    "class NotFound(Exception):\n    pass\n\n\n"
    "class Reader(Protocol):\n"
    "    def read(self, key: str) -> str:\n        ...\n\n"
    "    def close(self) -> None:\n        raise NotImplementedError\n"
)
BEHAVIOUR = (
    "def total(values, start=0):\n"
    "    result = start\n"
    "    for value in values:\n"
    "        if value:\n"
    "            result += value\n"
    "    return result\n"
)


def test_a_module_of_declarations_has_no_depth(tmp_path: Path):
    # Arrange
    write_tree(tmp_path, {"app/__init__.py": "", "app/types.py": DECLARATIONS})
    module = labelled_packages.read_original_tree(tmp_path).tree.by_directory["app"][-1]

    # Act
    surface = member_surfaces.read_surface(module)

    # Assert
    assert surface.implementation_size == 0
    assert surface.depth == 0.0
    assert surface.public == {"LIMIT", "NotFound", "Reader"}
    assert surface.interface_size == 8


def test_depth_is_the_share_of_statements_behind_the_interface(tmp_path: Path):
    # Arrange
    write_tree(tmp_path, {"app/__init__.py": "", "app/sums.py": BEHAVIOUR})
    module = labelled_packages.read_original_tree(tmp_path).tree.by_directory["app"][-1]

    # Act
    surface = member_surfaces.read_surface(module)

    # Assert
    assert surface.interface_size == 3
    assert surface.implementation_size == 5
    assert surface.depth == 5 / 8


def test_a_declaration_file_every_client_imports_by_name_is_idle(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/domain/errors.py": "class NotFound(Exception):\n    pass\n",
            "app/domain/limits.py": "LIMIT = 3\n",
            "app/domain/sums.py": BEHAVIOUR,
            "app/service.py": (
                "from app.domain.errors import NotFound\n"
                "from app.domain.limits import LIMIT\n\n\n"
                "def check(count):\n"
                "    if count > LIMIT:\n"
                "        raise NotFound\n\n\n"
                "def describe():\n"
                "    return LIMIT\n"
            ),
        },
    )
    boundaries = read_package_boundaries(tmp_path, package="app/domain")

    # Act
    idle = idle_boundaries.measure_idle_share(boundaries)
    co_use = boundary_signals.measure_scope_co_use(boundaries)
    hops = idle_boundaries.measure_hops(boundaries)

    # Assert
    assert idle == 2 / 3
    assert co_use == 0.5
    assert hops == 0.5


def test_a_member_used_as_a_namespace_or_holding_a_colliding_name_is_not_idle(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/api/__init__.py": "",
            "app/api/users.py": "ROUTES = ['users']\nPREFIX = '/users'\n",
            "app/api/items.py": "ROUTES = ['items']\n",
            "app/api/health.py": "STATUS = 'ok'\n",
            "app/api/limits.py": "LIMIT = 3\n",
            "app/main.py": (
                "from app.api import health\n"
                "from app.api.users import ROUTES\n"
                "from app.api.limits import LIMIT\n\n\n"
                "def read_status():\n"
                "    return health.STATUS, ROUTES, LIMIT\n"
            ),
        },
    )
    boundaries = read_package_boundaries(tmp_path, package="app/api")

    # Act
    colliding = boundary_signals.list_colliding_members(boundaries)
    qualified = idle_boundaries.list_qualified_members(boundaries)
    idle = idle_boundaries.measure_idle_share(boundaries)
    addressing = boundary_signals.measure_addressing(boundaries)

    # Assert
    assert colliding == {"app/api/users.py", "app/api/items.py"}
    assert qualified == {"app/api/health.py"}
    assert idle == 1 / 4
    assert addressing == {"qualified_use_share": 1 / 3, "facade_use_share": 0.0}


def test_a_repeated_assignment_nothing_imports_is_not_a_collision(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/jobs/__init__.py": "",
            "app/jobs/send.py": "import logging\n\nlogger = logging.getLogger(__name__)\nSEND = 1\n",
            "app/jobs/fetch.py": "import logging\n\nlogger = logging.getLogger(__name__)\nFETCH = 2\n",
            "app/main.py": "from app.jobs.send import SEND\nfrom app.jobs.fetch import FETCH\n",
        },
    )
    boundaries = read_package_boundaries(tmp_path, package="app/jobs")

    # Act
    colliding = boundary_signals.list_colliding_members(boundaries)
    saving = boundary_signals.measure_merge_saving(boundaries)

    # Assert
    assert colliding == set()
    assert saving == 0.5


def test_facade_use_and_sibling_only_names_are_read_from_imports(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/health/__init__.py": "from .router import build_router\n",
            "app/health/checks.py": "def check_database():\n    return True\n",
            "app/health/router.py": (
                "from .checks import check_database\n\n\n"
                "def build_router():\n"
                "    return check_database()\n"
            ),
            "app/main.py": "from app.health import build_router\n",
        },
    )
    boundaries = read_package_boundaries(tmp_path, package="app/health")

    # Act
    addressing = boundary_signals.measure_addressing(boundaries)
    sibling_only = boundary_signals.measure_sibling_only_names(boundaries)
    shrink = idle_boundaries.measure_interface_shrink(boundaries)

    # Assert
    assert addressing == {"qualified_use_share": 0.0, "facade_use_share": 1.0}
    assert sibling_only == 0.5
    assert shrink == 0.5


@pytest.mark.parametrize(
    ("labels", "level"),
    [
        ({"a": "merge", "b": "merge"}, "merge"),
        ({"a": "merge", "b": "contested"}, "contested"),
        ({"a": "merge", "b": "keep"}, "contested"),
        ({"a": "keep", "b": "keep"}, "keep"),
    ],
)
def test_strict_combination_needs_every_rater_for_merge(labels, level):
    # Arrange
    ratings = dict(labels)

    # Act
    combined = report_confirmation.combine_strictly(ratings)

    # Assert
    assert combined == level


def test_ceiling_is_how_well_one_rater_orders_the_other_raters_merges():
    # Arrange
    labels = {
        "one": {"a": "merge", "b": "merge"},
        "two": {"a": "merge", "b": "contested"},
        "three": {"a": "keep", "b": "contested"},
        "four": {"a": "keep", "b": "keep"},
        "single": {"a": "merge"},
    }

    # Act
    ceiling = report_exploration.measure_ceiling(labels)

    # Assert
    assert ceiling["rater_b_predicting_rater_a"] == {"auc": 0.875, "merge": 2, "rest": 2}
    assert ceiling["rater_a_predicting_rater_b"] == {"auc": 5 / 6, "merge": 1, "rest": 3}


def test_a_name_the_package_file_defines_is_a_use_of_the_package_file(tmp_path: Path):
    # Arrange
    write_tree(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/system/__init__.py": (
                "from .logger import build_logger\n\n\n"
                "def start_system():\n"
                "    return build_logger()\n"
            ),
            "app/system/logger.py": "def build_logger():\n    return object()\n",
            "app/main.py": "from app.system import start_system\n",
        },
    )
    boundaries = read_package_boundaries(tmp_path, package="app/system")

    # Act
    outside = boundaries.outside

    # Assert
    assert set(outside) == {"app/main.py"}
    assert outside["app/main.py"].names == {"app/system/__init__.py": {"start_system"}}
    assert outside["app/main.py"].facade == set()


SHOP = {
    "app/__init__.py": "",
    "app/shop/__init__.py": "",
    "app/shop/pricing.py": (
        "from decimal import Decimal\n\n"
        "RATE = Decimal('0.2')\n\n\n"
        "def add_tax(amount):\n"
        "    return amount * (1 + RATE)\n\n\n"
        "def format_price(amount):\n"
        "    return str(Decimal(amount))\n"
    ),
    "app/shop/errors.py": (
        "class ShopError(Exception):\n    pass\n\n\nclass OutOfStock(ShopError):\n    pass\n"
    ),
    "app/shop/stock.py": (
        "from app.shop.errors import OutOfStock\n\n\n"
        "def reserve(count):\n"
        "    if count < 1:\n"
        "        raise OutOfStock\n"
        "    return count\n"
    ),
    "app/checkout.py": (
        "from app.shop.pricing import add_tax, format_price\n"
        "from app.shop.stock import reserve\n\n\n"
        "def show_total(amount):\n"
        "    return format_price(add_tax(amount))\n\n\n"
        "def take(count):\n"
        "    return reserve(count)\n"
    ),
}


def read_shop(root: Path) -> tuple[definition_pairs.PackageDefinitions, dict]:
    """The definitions of the shop package and its tree's term rarity."""
    write_tree(root, SHOP)
    tree = measure_colocation.TreeDefinitions(labelled_packages.read_original_tree(root))
    return tree.read_package("app/shop"), tree.rarity


def test_references_are_read_within_a_module_and_across_sibling_modules(tmp_path: Path):
    # Arrange
    found, _rarity = read_shop(tmp_path)

    # Act
    references = found.references

    # Assert
    assert references == {
        (("app/shop/pricing.py", "add_tax"), ("app/shop/pricing.py", "RATE")),
        (("app/shop/errors.py", "OutOfStock"), ("app/shop/errors.py", "ShopError")),
        (("app/shop/stock.py", "reserve"), ("app/shop/errors.py", "OutOfStock")),
    }


def test_client_scopes_name_the_definitions_each_client_function_uses(tmp_path: Path):
    # Arrange
    found, _rarity = read_shop(tmp_path)

    # Act
    scopes = found.scopes

    # Assert
    assert scopes[("app/shop/pricing.py", "add_tax")] == {"app/checkout.py:5", "app/checkout.py"}
    assert scopes[("app/shop/stock.py", "reserve")] == {"app/checkout.py:9", "app/checkout.py"}
    assert ("app/shop/errors.py", "OutOfStock") not in scopes


def test_pair_features_do_not_depend_on_which_file_a_definition_sits_in(tmp_path: Path):
    # Arrange
    found, rarity = read_shop(tmp_path)
    by_name = {definition.name: definition for definition in found.definitions}

    # Act
    together = definition_pairs.measure_pair(
        found=found,
        first=by_name["add_tax"],
        second=by_name["format_price"],
        rarity=rarity,
    )
    apart = definition_pairs.measure_pair(
        found=found,
        first=by_name["reserve"],
        second=by_name["OutOfStock"],
        rarity=rarity,
    )

    # Assert
    assert together["client_co_use"] == 2 / 2
    assert together["reference"] == 0.0
    assert together["same_kind"] == 1.0
    assert apart["reference"] == 1.0
    assert apart["same_kind"] == 0.0
    assert apart["has_clients"] == 1.0


def test_sampled_pairs_cover_every_module_and_cap_the_cross_module_ones(tmp_path: Path):
    # Arrange
    found, _rarity = read_shop(tmp_path)

    # Act
    pairs = measure_colocation.sample_pairs(found=found, generator=random.Random(1))

    # Assert
    same = [pair for pair in pairs if pair[0].module == pair[1].module]
    assert len(same) == 3 + 1
    assert len(pairs) - len(same) == 3 * 2 + 3 * 1 + 2 * 1


def test_solver_and_fit_recover_a_known_system_and_a_known_direction():
    # Arrange
    generator = random.Random(1)
    rows = [[generator.gauss(0, 1), generator.gauss(0, 1)] for _index in range(600)]
    labels = [row[0] - row[1] + generator.gauss(0, 0.5) > 0 for row in rows]

    # Act
    solution = logistic.solve(matrix=[[2.0, 1.0], [1.0, 3.0]], vector=[3.0, 5.0])
    model = logistic.fit(rows=rows, labels=labels)

    # Assert
    assert solution == pytest.approx([0.8, 1.4])
    assert model.weights[0] > 1.0
    assert model.weights[1] < -1.0
    assert model.score([2.0, -2.0]) > 0.95
    assert model.score([-2.0, 2.0]) < 0.05


def build_score(*, tree: str, boundary: float, cohesion: float) -> dict[str, object]:
    """One scored package of two small modules of three definitions each."""
    module = {"lines": 40, "definitions": 3, "cohesion_score": cohesion}
    return {
        "tree": tree,
        "package": "app/shop",
        "lines": 80,
        "members": 2,
        "cross_pairs": 9,
        "boundary_score": boundary,
        "modules": [{"module": "a.py", **module}, {"module": "b.py", **module}],
    }


def test_construction_compares_fragmented_modules_with_authored_packages_per_tree():
    # Arrange
    scores = [
        build_score(tree="one", boundary=0.3, cohesion=0.7),
        build_score(tree="one", boundary=0.4, cohesion=0.6),
        build_score(tree="two", boundary=0.8, cohesion=0.2),
        build_score(tree="two", boundary=0.9, cohesion=0.1),
        build_score(tree="lone", boundary=0.5, cohesion=0.5),
    ]

    # Act
    found = report_package_scores.compare_construction(scores)

    # Assert
    assert found["per_tree"] == {"one": 1.0, "two": 0.0}
    assert found["trees_above_half"] == 1
    assert found["fragmented_modules"] == 10
    assert found["authored_packages"] == 5


def test_arms_pool_each_snapshot_and_set_the_authors_beside_them():
    # Arrange
    scores = [
        build_score(tree="ab-control-1-stage1", boundary=0.5, cohesion=0.6),
        build_score(tree="ab-lanorme-1-stage1", boundary=0.3, cohesion=0.6),
        build_score(tree="ab-lanorme-2-stage1", boundary=0.4, cohesion=0.6),
        build_score(tree="django", boundary=0.2, cohesion=0.7),
    ]

    # Act
    found = report_package_scores.compare_arms(scores)

    # Assert
    assert found["auc_lanorme_above_control"] == 0.0
    assert found["groups"]["authors"]["across_modules"] == 0.2
    assert found["groups"]["lanorme"]["across_modules"] == pytest.approx(0.35)
    assert found["groups"]["control"]["within_module"] == pytest.approx(0.6)
    assert found["groups"]["lanorme"]["modules"] == 4
