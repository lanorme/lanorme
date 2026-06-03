"""Tests for the streaming_disconnect check (SSE-001)."""

from __future__ import annotations

from pathlib import Path

from lanorme import Status
from lanorme.checks.streaming_disconnect import StreamingDisconnectCheck


def _run(tmp_path: Path, body: str, *, enabled: bool = True) -> object:
    root = tmp_path / "src"
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(body, encoding="utf-8")
    check = StreamingDisconnectCheck()
    if enabled:
        check.configure(settings={"enabled": True})
    return check.run(src_root=str(root))


def test_sse001_does_not_fire_when_disabled(tmp_path: Path):
    # Arrange: a function that would warn if the check were on.
    body = (
        "async def stream():\n"
        "    return StreamingResponse(gen())\n"
    )

    # Act: use default state — no configure call, so enabled=False.
    check = StreamingDisconnectCheck()
    root = tmp_path / "src"
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(body, encoding="utf-8")
    result = check.run(src_root=str(root))

    # Assert: check is off; no warnings emitted.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_sse001_fires_on_streaming_without_disconnect_handling(tmp_path: Path):
    # Arrange: async function returning StreamingResponse with no safeguard.
    body = (
        "async def stream():\n"
        "    async def gen():\n"
        "        yield b'data'\n"
        "    return StreamingResponse(gen())\n"
    )

    # Act.
    result = _run(tmp_path, body, enabled=True)

    # Assert: one advisory warning, not a hard violation.
    assert result.status == Status.WARN
    assert len(result.warnings) == 1
    assert "SSE-001" in result.warnings[0].rule
    assert result.violations == []


def test_sse001_does_not_fire_when_cancelled_error_caught(tmp_path: Path):
    # Arrange: async function that catches CancelledError.
    body = (
        "import asyncio\n"
        "\n"
        "async def stream():\n"
        "    async def gen():\n"
        "        try:\n"
        "            yield b'data'\n"
        "        except asyncio.CancelledError:\n"
        "            return\n"
        "    return StreamingResponse(gen())\n"
    )

    # Act.
    result = _run(tmp_path, body, enabled=True)

    # Assert: CancelledError handler is sufficient; no warnings.
    assert result.status == Status.PASS
    assert result.warnings == []


def test_sse001_does_not_fire_when_is_disconnected_called(tmp_path: Path):
    # Arrange: async function that polls is_disconnected.
    body = (
        "async def stream(request):\n"
        "    async def gen():\n"
        "        if await request.is_disconnected():\n"
        "            return\n"
        "        yield b'data'\n"
        "    return StreamingResponse(gen())\n"
    )

    # Act.
    result = _run(tmp_path, body, enabled=True)

    # Assert: is_disconnected call is sufficient; no warnings.
    assert result.status == Status.PASS
    assert result.warnings == []
