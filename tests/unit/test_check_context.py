"""Unit tests for CheckContext and the AST cache built by run_all().

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert``
markers so the AAA-001 check passes when LaNorme dogfoods itself.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from lanorme import CheckContext, CheckResult, Status, register, run_all


@dataclass
class _ContextCapturingCheck:
    """Stub check that records the context it receives."""

    name: str = "ctx_capture_stub"
    description: str = "captures context for inspection"
    rules: list[str] = field(default_factory=lambda: ["CTX-001: nothing"])
    received_ctx: CheckContext | None = field(default=None, init=False, repr=False)

    def run_with_context(self, *, ctx: CheckContext) -> CheckResult:
        self.received_ctx = ctx
        return CheckResult(check=self.name, status=Status.PASS)


@dataclass
class _LegacyCheck:
    """Stub check that only implements the old ``run`` interface."""

    name: str = "legacy_stub"
    description: str = "legacy check without run_with_context"
    rules: list[str] = field(default_factory=lambda: ["LEG-001: nothing"])
    was_called: bool = field(default=False, init=False, repr=False)

    def run(self, *, src_root: str) -> CheckResult:
        self.was_called = True
        return CheckResult(check=self.name, status=Status.PASS)


def test_run_all_builds_ast_cache(tmp_path: Path) -> None:
    # Arrange
    py_file = tmp_path / "sample.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    capture = _ContextCapturingCheck(name="ctx_capture_ast_test")
    register(capture)

    # Act
    run_all(src_root=str(tmp_path))

    # Assert
    assert capture.received_ctx is not None
    assert str(py_file) in capture.received_ctx.ast_cache
    cached = capture.received_ctx.ast_cache[str(py_file)]
    assert isinstance(cached, ast.Module)


def test_run_all_skips_syntax_error_files(tmp_path: Path) -> None:
    # Arrange
    bad_file = tmp_path / "broken.py"
    bad_file.write_text("def (:\n", encoding="utf-8")
    capture = _ContextCapturingCheck(name="ctx_capture_syntax_test")
    register(capture)

    # Act — must not raise
    results = run_all(src_root=str(tmp_path))

    # Assert
    assert any(r.check == "ctx_capture_syntax_test" for r in results)
    assert capture.received_ctx is not None
    assert str(bad_file) not in capture.received_ctx.ast_cache


def test_run_all_calls_legacy_check_with_src_root(tmp_path: Path) -> None:
    # Arrange
    legacy = _LegacyCheck(name="legacy_compat_test")
    register(legacy)

    # Act
    results = run_all(src_root=str(tmp_path))

    # Assert
    assert legacy.was_called
    assert any(r.check == "legacy_compat_test" for r in results)


def test_check_context_defaults() -> None:
    # Arrange / Act
    ctx = CheckContext(src_root="/some/root")

    # Assert
    assert ctx.src_root == "/some/root"
    assert ctx.ast_cache == {}


def test_check_context_ast_cache_is_independent_per_instance() -> None:
    # Arrange
    ctx_a = CheckContext(src_root="/a")
    ctx_b = CheckContext(src_root="/b")

    # Act
    ctx_a.ast_cache["key"] = object()

    # Assert
    assert "key" not in ctx_b.ast_cache
