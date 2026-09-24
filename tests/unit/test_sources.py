"""The shared parse layer: one read and parse per file, decoded like the interpreter.

Every AST check goes through ``lanorme.sources``, so its policy is pinned
here once: a BOM or coding cookie is honoured, a file the parser rejects or
overflows on becomes an ``UnparseableFile`` with a stable reason rather than an
exception, and a cached tree is never served stale.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lanorme import Status, sources
from lanorme.checks.file_limits import FileLimitsCheck
from lanorme.cli import main
from lanorme.sources import Module, UnparseableFile, iter_modules, parse_module


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_bom_and_coding_cookie_files_are_parsed(tmp_path: Path):
    # Arrange: two files the interpreter runs happily but a utf-8 ``read_text`` rejects.
    bom = _write(tmp_path / "bom.py", b"\xef\xbb\xbfx = 1\n")
    latin = _write(tmp_path / "latin.py", b"# -*- coding: latin-1 -*-\nname = '\xe9'\n")

    # Act.
    modules = {m.path.name: m for m in iter_modules(tmp_path)}

    # Assert: both are Modules with clean text (no BOM) and a tree.
    assert isinstance(modules["bom.py"], Module) and modules["bom.py"].source == "x = 1\n"
    assert isinstance(modules["latin.py"], Module)
    assert modules["latin.py"].source.endswith("name = 'é'\n")
    assert all(isinstance(m.tree, ast.Module) for m in modules.values())
    assert bom.exists() and latin.exists()


def test_syntax_error_yields_unparseable_with_parse_error_reason(tmp_path: Path):
    # Arrange.
    _write(tmp_path / "broken.py", b"def f(:\n")

    # Act.
    (module,) = list(iter_modules(tmp_path))

    # Assert.
    assert isinstance(module, UnparseableFile)
    assert module.reason == sources.PARSE_ERROR
    assert module.relative == "broken.py"


def test_parser_overflow_yields_unparseable_not_an_exception(tmp_path: Path, monkeypatch):
    # Arrange: the parser itself overflows (a 20k-term expression does this for real).
    path = _write(tmp_path / "deep.py", b"x = 1\n")

    def overflow(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded during ast construction")

    monkeypatch.setattr(sources.ast, "parse", overflow)

    # Act.
    module = parse_module(path, root=tmp_path)

    # Assert.
    assert isinstance(module, UnparseableFile) and module.reason == sources.TOO_DEEP


def test_parser_overflow_reports_a_skip_notice_not_a_check_crash(tmp_path: Path, monkeypatch):
    """One pathological file must not blank a check or flip the exit code to 0."""
    # Arrange: a real finding beside a file the parser overflows on.
    _write(tmp_path / "fat.py", b"def f(a, b, c, d, e, f, g, h, i):\n    return a\n")
    deep = _write(tmp_path / "deep.py", b"x = 1\n")
    real_parse = ast.parse

    def overflow(source, *args, **kwargs):
        if kwargs.get("filename", "").endswith("deep.py"):
            raise RecursionError("maximum recursion depth exceeded during ast construction")
        return real_parse(source, *args, **kwargs)

    monkeypatch.setattr(sources.ast, "parse", overflow)

    # Act.
    result = FileLimitsCheck().run(src_root=str(tmp_path))

    # Assert: the PARAM-001 error survives, the skipped file is a -000 notice.
    assert result.status == Status.FAIL
    assert [v.code for v in result.violations] == ["PARAM-001"]
    assert [w.rule for w in result.warnings] == ["SIZE-000: too deeply nested"]
    assert result.warnings[0].file == deep.name


def test_cache_serves_the_same_tree_until_the_file_changes(tmp_path: Path):
    # Arrange.
    path = _write(tmp_path / "m.py", b"x = 1\n")
    first = parse_module(path, root=tmp_path)

    # Act: parse again unchanged, then after a rewrite that changes the size.
    again = parse_module(path, root=tmp_path)
    _write(path, b"x = 1\ny = 2\n")
    changed = parse_module(path, root=tmp_path)

    # Assert: identical object while unchanged, a fresh tree once the file moved on.
    assert isinstance(first, Module) and again.tree is first.tree
    assert isinstance(changed, Module) and changed.tree is not first.tree
    assert len(changed.tree.body) == 2


def test_clear_cache_empties_it(tmp_path: Path):
    # Arrange.
    parse_module(_write(tmp_path / "m.py", b"x = 1\n"), root=tmp_path)
    assert sources.count_cached() == 1

    # Act.
    sources.clear_cache()

    # Assert.
    assert sources.count_cached() == 0


def test_every_check_shares_one_parse_per_file(tmp_path: Path, monkeypatch, capsys):
    """The whole CLI run reads and parses each file once, whatever the check count."""
    # Arrange: count parser calls across a full run over two files.
    _write(tmp_path / "a.py", b"x = 1\n")
    _write(tmp_path / "b.py", b"y = 2\n")
    real_parse = ast.parse
    calls: list[str] = []

    def counting(source, *args, **kwargs):
        calls.append(kwargs.get("filename", ""))
        return real_parse(source, *args, **kwargs)

    monkeypatch.setattr(sources.ast, "parse", counting)

    # Act.
    try:
        main(["check", str(tmp_path)])
    except SystemExit:
        pass
    capsys.readouterr()

    # Assert: one parse per file, none repeated.
    assert sorted(Path(name).name for name in calls) == ["a.py", "b.py"]
