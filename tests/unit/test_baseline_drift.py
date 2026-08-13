"""A baseline entry whose anchor moved must not read as new debt.

A file-level finding anchors on a hash of its rule description, so an upgrade
that rewords a rule silently detaches every entry recording it: the run goes red
on code nobody touched. `check` says so rather than leaving the user to guess,
and these pin both halves of that promise, the notice and its silence.

Drift is simulated by rewriting a written baseline's anchors, which is what a
reworded rule description does to an existing file.
"""

from __future__ import annotations

import json
from pathlib import Path

from lanorme.cli import main

_WIDE_FUNCTION = "def f(p0, p1, p2, p3, p4, p5, p6, p7, p8):\n    return 0\n"
_SECOND_WIDE_FUNCTION = "\n\ndef g(q0, q1, q2, q3, q4, q5, q6, q7, q8):\n    return 1\n"


def _project(root: Path, body: str) -> Path:
    """A one-file project that reports PARAM-001, with a baseline configured."""
    (root / "lanorme.toml").write_text(
        'select = ["PARAM-001"]\nbaseline = "lanorme-baseline.json"\n', encoding="utf-8"
    )
    source = root / "sample.py"
    source.write_text(body, encoding="utf-8")
    return source


def _run(root: Path, *extra: str) -> None:
    """Run a command, swallowing the exit signal."""
    try:
        main([*extra, str(root)]) if extra else main(["check", str(root)])
    except SystemExit:
        pass


def _write_baseline(root: Path) -> None:
    _run(root, "baseline", "write")


def _detach_anchors(root: Path) -> None:
    """Rewrite every anchor so the entries record the same findings but match none."""
    path = root / "lanorme-baseline.json"
    recorded = json.loads(path.read_text())
    for entry in recorded["entries"]:
        entry["anchor"] = "desc:" + "0" * 64
    path.write_text(json.dumps(recorded, indent=2), encoding="utf-8")


def _drifted_project(root: Path) -> None:
    """A project whose baseline records its finding but no longer matches it."""
    _project(root, _WIDE_FUNCTION)
    _write_baseline(root)
    _detach_anchors(root)


def test_drifted_entry_is_explained(tmp_path: Path, capsys) -> None:
    # Arrange: a recorded finding whose anchor no longer matches.
    _drifted_project(tmp_path)

    # Act
    _run(tmp_path)

    # Assert
    out = capsys.readouterr().out
    assert "baseline entry that no longer matches" in out
    assert "sample.py  PARAM-001" in out
    assert "lanorme baseline write" in out


def test_healthy_baseline_says_nothing(tmp_path: Path, capsys) -> None:
    # Arrange
    _project(tmp_path, _WIDE_FUNCTION)
    _write_baseline(tmp_path)

    # Act
    _run(tmp_path)

    # Assert
    assert "no longer matches" not in capsys.readouterr().out


def test_new_debt_beside_a_matching_entry_says_nothing(tmp_path: Path, capsys) -> None:
    # The regression that matters: a second wide function is genuinely new, and
    # its file already carries a PARAM-001 entry that still matches. Reporting
    # on file and rule alone would call this existing debt, which is wrong.
    source = _project(tmp_path, _WIDE_FUNCTION)
    _write_baseline(tmp_path)
    source.write_text(_WIDE_FUNCTION + _SECOND_WIDE_FUNCTION, encoding="utf-8")

    # Act
    _run(tmp_path)

    # Assert
    out = capsys.readouterr().out
    assert "Function 'g'" in out, "the new finding should still report"
    assert "no longer matches" not in out


def test_machine_output_carries_no_notice(tmp_path: Path, capsys) -> None:
    # Arrange: --json is a finding stream that has to stay parseable.
    _drifted_project(tmp_path)
    capsys.readouterr()  # drop the setup chatter so the run's output stands alone

    # Act
    try:
        main(["check", str(tmp_path), "--json"])
    except SystemExit:
        pass

    # Assert
    out = capsys.readouterr().out
    assert "no longer matches" not in out
    json.loads(out)
