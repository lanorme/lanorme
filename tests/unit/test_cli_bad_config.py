"""A value a check rejects is a usage error, not a traceback.

Per-check settings come from a TOML table the user wrote by hand, so a value of
the wrong type is a configuration mistake. Every ``configure()`` coerces its
input (``int(settings[key])`` and friends), and an uncaught coercion failure
used to unwind a traceback out of the check. These pin the reporting contract:
exit 2, the offending table and key named on stderr, and nothing scanned.

The handling sits in the shared ``apply_check_config`` plumbing rather than in
any one check, so ``file_limits`` and ``comments`` are both exercised here to
keep it that way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme.cli import main


def _project(root: Path, config: str) -> None:
    """A one-file project carrying *config* as its whole lanorme.toml."""
    (root / "lanorme.toml").write_text(config, encoding="utf-8")
    (root / "sample.py").write_text("x = 1\n", encoding="utf-8")


def _run(root: Path) -> int:
    """Run ``check <root>`` and return the exit code.

    A clean run returns rather than raising, so the absence of ``SystemExit``
    is exit 0.
    """
    try:
        main(["check", str(root)])
    except SystemExit as exit_signal:
        return int(exit_signal.code or 0)
    return 0


@pytest.mark.parametrize(
    ("table", "key", "value"),
    [
        ("file_limits", "file_error_lines", '"lots"'),
        ("file_limits", "param_warn", "[1, 2]"),
        ("comments", "max_block_lines", '"lots"'),
    ],
)
def test_uncoercible_value_exits_two_and_names_the_key(
    tmp_path: Path, capsys, table: str, key: str, value: str
) -> None:
    # Arrange
    _project(tmp_path, f"[{table}]\n{key} = {value}\n")

    # Act
    code = _run(tmp_path)

    # Assert
    captured = capsys.readouterr()
    assert code == 2
    assert f"[tool.lanorme.{table}] {key}" in captured.err
    assert "Traceback" not in captured.err


def test_uncoercible_value_in_a_nested_region_is_reported(tmp_path: Path, capsys) -> None:
    # Arrange: the root config is fine and the nested one is not, so this only
    # passes if the cascading runner reports through the same path.
    _project(tmp_path, 'select = ["SIZE-001"]\n')
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "lanorme.toml").write_text(
        '[file_limits]\nfile_error_lines = "nope"\n', encoding="utf-8"
    )
    (nested / "mod.py").write_text("y = 1\n", encoding="utf-8")

    # Act
    code = _run(tmp_path)

    # Assert
    assert code == 2
    assert "[tool.lanorme.file_limits] file_error_lines" in capsys.readouterr().err


def test_a_valid_table_is_untouched(tmp_path: Path, capsys) -> None:
    # Arrange: the guard must not turn a good config into an error.
    _project(tmp_path, "[file_limits]\nfile_error_lines = 400\n")

    # Act
    code = _run(tmp_path)

    # Assert
    assert code == 0
    assert "ERROR" not in capsys.readouterr().err
