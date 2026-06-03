"""Smoke tests for SSE-001: streaming endpoint disconnect safety.

Each rule has a positive (must fire) and a negative (must not fire) case
to lock in the AST-shape contract from day one.
"""

from __future__ import annotations

from lanorme.checks.streaming_safety import StreamingSafetyCheck


def _codes(violations) -> set[str]:
    return {v.rule for v in violations}


# --- Positive cases (SSE-001 must fire) ---


def test_sse001_fires_on_async_generator_without_handling(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="endpoint.py",
        body=(
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "async def event_generator():\n"
            "    while True:\n"
            '        yield "data: hello\\n\\n"\n'
            "\n"
            "async def endpoint():\n"
            '    return StreamingResponse(event_generator(), media_type="text/event-stream")\n'
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" in _codes(result.violations)


def test_sse001_fires_on_sync_generator_without_handling(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="stream.py",
        body=(
            "from starlette.responses import StreamingResponse\n"
            "\n"
            "def generate_data():\n"
            "    for chunk in range(10):\n"
            '        yield f"chunk {chunk}"\n'
            "\n"
            "def endpoint():\n"
            "    return StreamingResponse(generate_data())\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" in _codes(result.violations)


def test_sse001_fires_on_event_source_response(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="sse.py",
        body=(
            "from sse_starlette.sse import EventSourceResponse\n"
            "\n"
            "async def gen():\n"
            '    yield {"data": "hello"}\n'
            "\n"
            "async def stream():\n"
            "    return EventSourceResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" in _codes(result.violations)


def test_sse001_fires_on_content_keyword_arg_without_handling(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="stream.py",
        body=(
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "async def gen():\n"
            '    yield "chunk"\n'
            "\n"
            "async def endpoint():\n"
            '    return StreamingResponse(content=gen(), media_type="text/plain")\n'
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" in _codes(result.violations)


# --- Negative cases (SSE-001 must not fire) ---


def test_sse001_silent_on_asyncio_cancelled_error_handler(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "import asyncio\n"
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "async def event_generator():\n"
            "    try:\n"
            "        while True:\n"
            '            yield "data: hello\\n\\n"\n'
            "    except asyncio.CancelledError:\n"
            "        return\n"
            "\n"
            "async def endpoint():\n"
            "    return StreamingResponse(event_generator())\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse001_silent_on_bare_cancelled_error_handler(tmp_path, tmp_py_file):
    # Arrange - CancelledError imported directly (no asyncio. prefix)
    tmp_py_file(
        name="ok.py",
        body=(
            "from asyncio import CancelledError\n"
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "async def gen():\n"
            "    try:\n"
            "        yield 'data'\n"
            "    except CancelledError:\n"
            "        return\n"
            "\n"
            "async def endpoint():\n"
            "    return StreamingResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse001_silent_on_bare_except(tmp_path, tmp_py_file):
    # Arrange - bare except catches CancelledError too
    tmp_py_file(
        name="ok.py",
        body=(
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "async def gen():\n"
            "    try:\n"
            "        while True:\n"
            '            yield "data"\n'
            "    except:\n"
            "        pass\n"
            "\n"
            "async def endpoint():\n"
            "    return StreamingResponse(gen())\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse001_silent_on_is_disconnected_check(tmp_path, tmp_py_file):
    # Arrange
    tmp_py_file(
        name="ok.py",
        body=(
            "from fastapi import Request\n"
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "async def event_generator(request):\n"
            "    while True:\n"
            "        if await request.is_disconnected():\n"
            "            break\n"
            '        yield "data: hello\\n\\n"\n'
            "\n"
            "async def endpoint(request: Request):\n"
            "    return StreamingResponse(event_generator(request))\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse001_silent_on_non_local_generator(tmp_path, tmp_py_file):
    # Arrange - generator not defined in this file
    tmp_py_file(
        name="endpoint.py",
        body=(
            "from fastapi.responses import StreamingResponse\n"
            "from my_module import generate_events\n"
            "\n"
            "async def endpoint():\n"
            "    return StreamingResponse(generate_events())\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse001_silent_on_non_streaming_response(tmp_path, tmp_py_file):
    # Arrange - generator exists but response is not streaming
    tmp_py_file(
        name="endpoint.py",
        body=(
            "from fastapi.responses import JSONResponse\n"
            "\n"
            "def generate_data():\n"
            "    yield {'key': 'value'}\n"
            "\n"
            "async def endpoint():\n"
            "    return JSONResponse({'message': 'ok'})\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)


def test_sse001_silent_on_generator_expression_arg(tmp_path, tmp_py_file):
    # Arrange - generator expression (not a function call) is not flagged
    tmp_py_file(
        name="stream.py",
        body=(
            "from fastapi.responses import StreamingResponse\n"
            "\n"
            "data = [1, 2, 3]\n"
            "\n"
            "async def endpoint():\n"
            "    return StreamingResponse(str(x) for x in data)\n"
        ),
    )

    # Act
    result = StreamingSafetyCheck().run(src_root=str(tmp_path))

    # Assert
    assert "SSE-001" not in _codes(result.violations)
