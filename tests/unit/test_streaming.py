"""Smoke tests for SSE-001: streaming endpoints must handle client disconnect."""

from __future__ import annotations

from lanorme.checks.streaming import StreamingCheck


def _codes(violations) -> set[str]:
    return {v.rule for v in violations}


def test_sse_001_fires_on_streaming_response_without_handler(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body=(
            "from starlette.responses import StreamingResponse\n"
            "async def gen():\n"
            "    for i in range(10):\n"
            "        yield f'data: {i}\\n\\n'\n"
            "def endpoint():\n"
            "    return StreamingResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" in _codes(result.violations)


def test_sse_001_fires_on_event_source_response(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="bad.py",
        body=(
            "from sse_starlette.sse import EventSourceResponse\n"
            "async def generate():\n"
            "    for msg in messages:\n"
            "        yield msg\n"
            "def view():\n"
            "    return EventSourceResponse(generate())\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" in _codes(result.violations)


def test_sse_001_accepts_asyncio_cancelled_error_handler(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "import asyncio\n"
            "from starlette.responses import StreamingResponse\n"
            "async def gen():\n"
            "    try:\n"
            "        for i in range(10):\n"
            "            yield f'data: {i}\\n\\n'\n"
            "    except asyncio.CancelledError:\n"
            "        return\n"
            "def endpoint():\n"
            "    return StreamingResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse_001_accepts_is_disconnected_check(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "from starlette.responses import StreamingResponse\n"
            "async def gen(request):\n"
            "    for i in range(10):\n"
            "        if await request.is_disconnected():\n"
            "            break\n"
            "        yield f'data: {i}\\n\\n'\n"
            "def endpoint(request):\n"
            "    return StreamingResponse(gen(request))\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse_001_accepts_bare_except(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "from starlette.responses import StreamingResponse\n"
            "async def gen():\n"
            "    try:\n"
            "        for i in range(10):\n"
            "            yield str(i)\n"
            "    except:\n"
            "        return\n"
            "def endpoint():\n"
            "    return StreamingResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse_001_does_not_fire_on_non_streaming_async_generator(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "async def gen():\n"
            "    for i in range(10):\n"
            "        yield i\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse_001_does_not_fire_when_generator_has_no_yield(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "from starlette.responses import StreamingResponse\n"
            "async def build_content():\n"
            "    return b'hello'\n"
            "def endpoint():\n"
            "    return StreamingResponse(build_content())\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse_001_accepts_base_exception_handler(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "from starlette.responses import StreamingResponse\n"
            "async def gen():\n"
            "    try:\n"
            "        for i in range(10):\n"
            "            yield str(i)\n"
            "    except BaseException:\n"
            "        return\n"
            "def endpoint():\n"
            "    return StreamingResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)
