"""Contract pins for check behaviour that a tautology audit found unguarded.

Each test here killed a mutant that survived the whole suite: a check's
``run()`` is called on a real tree and the exact ``(code, file, line)`` of what
it reports is asserted, so a threshold that drifts off its boundary, a lost
test-file exemption, or a finding reported one line off fails here.
"""

from __future__ import annotations

from pathlib import Path

from lanorme.checks.file_limits import FileLimitsCheck
from lanorme.checks.layer_deps import LayerDepsCheck
from lanorme.checks.named_args import NamedArgsCheck
from lanorme.checks.similarity import SimilarityCheck
from lanorme.checks.strong_types import StrongTypesCheck

# Two structurally identical six-statement functions, the SIMILAR-001 shape.
_CLONE_PAIR = (
    "def a(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n\n"
    "def b(s):\n x = s.alpha\n y = s.beta\n z = combine(x, y)\n w = z * 2\n return w\n"
)


def _build_similarity_check(**settings: object) -> SimilarityCheck:
    check = SimilarityCheck()
    check.configure(settings={"enabled": True, **settings})
    return check


def test_size_rules_skip_test_prefixed_files_but_not_their_twin(tmp_path: Path):
    # Arrange: the same 510-line body under a test_ name and a plain name.
    body = "".join(f"x{i} = {i}\n" for i in range(510))
    (tmp_path / "test_big.py").write_text(body, encoding="utf-8")
    (tmp_path / "big.py").write_text(body, encoding="utf-8")

    # Act
    result = FileLimitsCheck().run(src_root=str(tmp_path))

    # Assert: only the plain file is over the limit; the test file is exempt.
    assert [(v.code, v.file, v.line) for v in result.violations] == [("SIZE-001", "big.py", 1)]
    assert "510 effective lines" in result.violations[0].message
    assert not [w for w in result.warnings if w.file == "test_big.py"]


def test_struct_ratio_threshold_is_inclusive(tmp_path: Path):
    # Arrange: an exact structural clone against a ratio of 1.0, the boundary.
    (tmp_path / "m.py").write_text(_CLONE_PAIR, encoding="utf-8")
    check = _build_similarity_check(struct_ratio=1.0)

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert: a pair at the threshold is reported.
    assert [(w.code, w.file, w.line) for w in result.warnings] == [("SIMILAR-001", "m.py", 1)]
    assert "'a' and 'b'" in result.warnings[0].message


def test_similarity_skips_test_prefixed_files(tmp_path: Path):
    # Arrange: the clone pair in a test_ file and in a plain file.
    (tmp_path / "test_m.py").write_text(_CLONE_PAIR, encoding="utf-8")
    (tmp_path / "m.py").write_text(_CLONE_PAIR, encoding="utf-8")
    check = _build_similarity_check()

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert {w.file for w in result.warnings} == {"m.py"}


def test_findings_follow_source_order_across_def_and_async_def(tmp_path: Path):
    # Arrange: an async def above a plain def, both over the parameter limit.
    params = ", ".join(f"p{i}" for i in range(9))
    (tmp_path / "m.py").write_text(
        f"async def first({params}):\n    return p0\n\n\ndef second({params}):\n    return p0\n",
        encoding="utf-8",
    )

    # Act
    result = FileLimitsCheck().run(src_root=str(tmp_path))

    # Assert: reported in source order, not grouped by node type.
    assert [(v.code, v.line) for v in result.violations] == [("PARAM-001", 1), ("PARAM-001", 5)]


def test_type001_is_reported_at_the_def_line(tmp_path: Path):
    # Arrange: a dict[str, Any] parameter on line 4.
    (tmp_path / "m.py").write_text(
        "from typing import Any\n\n\ndef handler(payload: dict[str, Any]) -> None: ...\n",
        encoding="utf-8",
    )

    # Act
    result = StrongTypesCheck().run(src_root=str(tmp_path))

    # Assert
    [hit] = [v for v in result.violations if v.code == "TYPE-001"]
    assert (hit.file, hit.line) == ("m.py", 4)
    assert "payload" in hit.message


def test_layer005_is_reported_at_the_import_line(tmp_path: Path):
    # Arrange: an api module importing infrastructure on line 3.
    for rel, body in {
        "api/routes.py": '"""Routes."""\n\nfrom infrastructure.db import Session\n',
        "infrastructure/db.py": "class Session: ...\n",
        "domain/models.py": "class Entity: ...\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")

    # Act
    result = LayerDepsCheck().run(src_root=str(tmp_path))

    # Assert
    assert [(v.code, v.file, v.line) for v in result.violations] == [
        ("LAYER-005", "api/routes.py", 3),
    ]


def test_kwarg001_is_reported_at_the_def_line(tmp_path: Path):
    # Arrange: a two-positional-parameter def on line 3, with the check enabled.
    (tmp_path / "tp.py").write_text(
        "\n\ndef transfer(amount, currency):\n    return amount\n",
        encoding="utf-8",
    )
    check = NamedArgsCheck()
    check.enabled = True

    # Act
    result = check.run(src_root=str(tmp_path))

    # Assert
    assert [(v.code, v.file, v.line) for v in result.violations] == [("KWARG-001", "tp.py", 3)]
