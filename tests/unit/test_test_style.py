"""Unit tests for the AAA-001 / AAA-002 (``test_style``) check itself.

Each test follows AAA structure with explicit comment markers so it stays
green under its own enforcement when LaNorme runs against tests/.
"""

from __future__ import annotations

from lanorme.checks.test_style import TestStyleCheck


def _rule_codes(violations) -> set[str]:
    return {v.rule for v in violations}


def test_short_test_function_is_exempt_from_aaa_markers(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(name="test_short.py", body="def test_one():\n    x = 1\n    assert x == 1\n")
    check = TestStyleCheck(enabled=True, min_statements=3)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "AAA-001" not in _rule_codes(result.violations)


def test_long_test_without_markers_triggers_aaa_001(tmp_path, tmp_py_file):
    # Arrange
    body = "def test_long():\n" + "".join(
        f"    a{i} = {i}\n" for i in range(8)
    ) + "    assert a0 == 0\n"
    tmp_py_file(name="test_long.py", body=body)
    check = TestStyleCheck(enabled=True, min_statements=3, required_markers=2)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "AAA-001" in _rule_codes(result.violations)


def test_test_with_arrange_and_assert_markers_passes(tmp_path, tmp_py_file):
    # Arrange
    body = (
        "def test_marked():\n"
        "    # Arrange\n"
        "    a = 1\n"
        "    b = 2\n"
        "    c = 3\n"
        "    # Assert\n"
        "    assert a + b + c == 6\n"
    )
    tmp_py_file(name="test_marked.py", body=body)
    check = TestStyleCheck(enabled=True, min_statements=3, required_markers=2)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "AAA-001" not in _rule_codes(result.violations)


def test_duplicate_arrange_prefix_triggers_aaa_002(tmp_path, tmp_py_file):
    # Arrange
    body = (
        "def test_one():\n"
        "    client = make_client()\n"
        "    user = make_user(client)\n"
        "    item = make_item(user)\n"
        "    assert item.id is not None\n"
        "\n"
        "def test_two():\n"
        "    client = make_client()\n"
        "    user = make_user(client)\n"
        "    item = make_item(user)\n"
        "    assert item.owner == user\n"
    )
    tmp_py_file(name="test_dup.py", body=body)
    check = TestStyleCheck(enabled=True, dry_prefix_statements=3, required_markers=1)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "AAA-002" in _rule_codes(result.violations)


def test_fixture_function_is_not_treated_as_a_test(tmp_path, tmp_py_file):
    # Arrange
    body = (
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def test_looks_like_a_test():\n"
        "    yield 1\n"
        "    a = 2\n"
        "    b = 3\n"
        "    c = 4\n"
        "    d = 5\n"
    )
    tmp_py_file(name="test_fixture.py", body=body)
    check = TestStyleCheck(enabled=True, min_statements=3, required_markers=2)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert "AAA-001" not in _rule_codes(result.violations)
