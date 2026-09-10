"""Tests for the naming vocabulary shared by the naming checks.

The lists are data, so the tests pin the invariants the rules rely on (the
precision-first list is a subset of the recall-first one, everything is
lower-case) and the tokenizer and verb tests on the exact shapes the rules
were calibrated against.
"""

from __future__ import annotations

import pytest

from lanorme.checks.naming_words import (
    VERB_CAPABLE,
    VERB_ONLY,
    WEAK_VERBS,
    is_noun_phrase,
    is_pascal_case,
    is_predicate,
    is_verb_capable,
    leading_verb_index,
    postposed_verb_index,
    split_name,
    verb_first,
)


def test_precision_list_is_a_subset_of_the_recall_list() -> None:
    assert VERB_ONLY <= VERB_CAPABLE
    assert WEAK_VERBS <= VERB_CAPABLE


def test_lists_are_lower_case_single_words() -> None:
    assert all(word == word.lower() and "_" not in word for word in VERB_CAPABLE)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("getHTTPResponse", ["get", "http", "response"]),
        ("_shell_violations", ["shell", "violations"]),
        ("SaveTest2", ["save", "test", "2"]),
        ("__init__", ["init"]),
        ("_", []),
        ("résumé_thing", []),
    ],
)
def test_split_name(name: str, expected: list[str]) -> None:
    assert split_name(name=name) == expected


@pytest.mark.parametrize(
    "word",
    ["parse", "matches", "applies", "simplify", "normalise", "validate", "reload",
     "unquote", "deregister", "getheaders", "setdefault", "startswith", "aclose", "autobegin",
     "mkdir", "rmtree", "chmod", "iteritems", "isdigit"],
)
def test_words_that_read_as_verbs(word: str) -> None:
    assert is_verb_capable(word=word)


@pytest.mark.parametrize(
    "word",
    ["shell", "finding", "logger", "sorted", "violations", "cert", "layout", "user", "h1",
     "password", "endpoint", "checksum", "template", "state", "noise", "settings", "getter"],
)
def test_words_that_do_not_read_as_verbs(word: str) -> None:
    assert not is_verb_capable(word=word)


def test_extra_verbs_extend_the_vocabulary() -> None:
    assert not is_verb_capable(word="frob")
    assert is_verb_capable(word="frob", extra=frozenset({"frob"}))


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["write"], 0),
        (["bulk", "insert", "rows"], 1),
        (["re", "apply", "assignments"], 1),
        (["cert", "verify"], -1),
        (["shell", "violations"], -1),
    ],
)
def test_leading_verb_index_skips_modifiers_only(tokens: list[str], expected: int) -> None:
    assert leading_verb_index(tokens=tokens) == expected


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["cert", "verify"], 1),
        (["user", "count", "update"], 2),
        (["bulk", "cert", "verify"], 2),
        (["lru", "size", "alert"], 2),
        (["h1", "finding"], -1),
        (["ports", "adapter"], -1),
        (["html", "beautify"], -1),
        (["write"], -1),
    ],
)
def test_postposed_verb_index_takes_the_last_listed_verb(tokens: list[str], expected: int) -> None:
    assert postposed_verb_index(tokens=tokens) == expected


@pytest.mark.parametrize(
    ("name", "index", "expected"),
    [
        ("_cert_verify2", 1, "_verify_cert2"),
        ("bulk_cert_verify", 2, "bulk_verify_cert"),
        ("user_count_update", 2, "update_user_count"),
    ],
)
def test_verb_first_keeps_prefix_modifiers_and_digits(name: str, index: int, expected: str) -> None:
    assert verb_first(name=name, tokens=split_name(name=name), index=index) == expected


def test_predicates_carry_an_auxiliary() -> None:
    assert is_predicate(tokens=["line", "has", "noqa"])
    assert is_predicate(tokens=["path", "is", "ancestor"])
    assert not is_predicate(tokens=["shell", "violations"])


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["fetch", "options"], True),
        (["connect", "timeout"], True),
        (["compile", "error"], True),
        (["load", "balancer"], True),
        (["save", "test", "2"], True),
        (["get", "children", "traversal"], True),
        (["parse", "result"], True),
        (["send", "2", "users"], False),
        (["fetch", "users"], False),
        (["send", "email"], False),
        (["validate", "order"], False),
        (["create", "connection"], False),
        (["remove", "events", "globally"], False),
    ],
)
def test_is_noun_phrase(tokens: list[str], expected: bool) -> None:
    assert is_noun_phrase(tokens=tokens) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("UserFetcher", True), ("_Settings", True), ("CONSOLE_INFO", False), ("ctypes_struct", False)],
)
def test_is_pascal_case(name: str, expected: bool) -> None:
    assert is_pascal_case(name=name) is expected
