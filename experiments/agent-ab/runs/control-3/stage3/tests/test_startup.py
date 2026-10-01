"""The real-model path and the uvicorn factory entry point (no model is ever called)."""

import os
import socket
import subprocess
import sys
import time

import httpx
import langchain.chat_models
from fastapi.testclient import TestClient

import app.main
from tests.fakes import ai, scripted


def test_import_does_not_build_a_model(monkeypatch):
    import importlib

    def boom(*a, **k):
        raise AssertionError("model built at import time")

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", boom)
    importlib.reload(app.main)


def test_model_from_env_when_none_given(monkeypatch):
    requested = []
    fake = scripted(ai("from env"))

    def fake_init(name, **kwargs):
        requested.append(name)
        return fake

    monkeypatch.setenv("AGENT_MODEL", "openai:my-model")
    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init)
    client = TestClient(app.main.create_app())
    r = client.post("/chat", json={"session_id": "s", "message": "hi"})
    assert requested == ["openai:my-model"]
    assert r.json()["reply"] == "from env"


def test_env_config_applies_to_app(monkeypatch):
    monkeypatch.setenv("BLOCKED_TOPICS", "gambling")
    client = TestClient(app.main.create_app(scripted(ai("ok"))))
    assert client.post("/chat", json={"session_id": "s", "message": "gambling"}).status_code == 403


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_factory_starts():
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except httpx.TransportError:
                assert proc.poll() is None, proc.stdout.read().decode()
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.2)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
