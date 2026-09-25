"""Shared classification of test files, used by every check that treats tests differently.

A check either exempts test code from a production rule (a test may poke
internals, hard-code a sample secret, or skip keyword-only separators) or
selects test code to judge it (the AAA rules, the coverage partner lookup).
Both kinds share the predicates below so a file cannot be a test for one
rule and production code for another.

Every predicate takes the path *relative to the scan root* (a ``str`` or
``Path``; either slash direction). Matching the root-relative parts, not
the absolute path's, keeps the user's filesystem above the root out of it:
a checkout that happens to live under a ``tests/`` directory is scanned
like any other.

- :func:`is_test_module`: a module pytest collects by name, ``test_*.py`` or
  ``*_test.py``, wherever it lives (pytest collects beside code too).
- :func:`is_test_support`: code that exists only to serve tests but is not
  itself collected: ``conftest.py`` anywhere, and a ``fixtures`` or
  ``factories`` module or package inside a tests directory.
- :func:`is_under_tests_dir`: anything below a ``tests`` or ``test``
  directory (helpers, fake servers, compatibility shims, test packages).
- :func:`is_test_file`: the union of the three, the definition the exempting
  checks use. "Test files" in ``docs/RULES.md`` lists the rules that use it.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

# Directory names that hold a test suite. The singular form is what the
# standard library and SQLAlchemy use; the plural is the pytest convention.
TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test"})

# Modules or packages inside a tests directory that hold shared fixtures
# rather than tests. Only honoured under a tests directory: a production
# ``factories/`` package (the factory pattern) is not test code.
TEST_SUPPORT_NAMES: frozenset[str] = frozenset({"fixtures", "factories"})

# pytest's own support module. It is test code wherever it lives.
TEST_SUPPORT_FILENAMES: frozenset[str] = frozenset({"conftest.py"})

# pytest's default ``python_files`` collection patterns, as (prefix, suffix).
_TEST_STEM_PREFIX = "test_"
_TEST_STEM_SUFFIX = "_test"


def _normalise_posix(relative: str | Path) -> PurePosixPath:
    """Normalise *relative* to a forward-slashed pure path."""
    return PurePosixPath(str(relative).replace("\\", "/"))


def is_test_module(relative: str | Path) -> bool:
    """True if the file is a module pytest collects by name.

    Matches a ``test_*.py`` or ``*_test.py`` stem anywhere in the tree,
    since pytest collects such a module beside production code as readily as
    under ``tests/``. ``conftest.py`` and ``__init__.py`` do not match.
    """
    stem = _normalise_posix(relative).stem
    return stem.startswith(_TEST_STEM_PREFIX) or stem.endswith(_TEST_STEM_SUFFIX)


def is_under_tests_dir(relative: str | Path) -> bool:
    """True if any directory on the relative path is ``tests`` or ``test``."""
    parts = _normalise_posix(relative).parts
    return any(part in TEST_DIR_NAMES for part in parts[:-1])


def is_test_support(relative: str | Path) -> bool:
    """True for test scaffolding that pytest does not collect as tests.

    ``conftest.py`` counts wherever it lives. A ``fixtures`` or ``factories``
    module or package counts only inside a tests directory, so a production
    package of the same name is still checked.
    """
    path = _normalise_posix(relative)
    if path.name in TEST_SUPPORT_FILENAMES:
        return True
    if not is_under_tests_dir(path):
        return False
    return path.stem in TEST_SUPPORT_NAMES or any(
        part in TEST_SUPPORT_NAMES for part in path.parts[:-1]
    )


def is_test_file(relative: str | Path) -> bool:
    """True if the file is test code by any of the three predicates.

    This is the definition a production rule uses to exempt tests: a
    collected test module, its support scaffolding, or anything under a tests
    directory.
    """
    path = _normalise_posix(relative)
    return is_test_module(path) or is_test_support(path) or is_under_tests_dir(path)
