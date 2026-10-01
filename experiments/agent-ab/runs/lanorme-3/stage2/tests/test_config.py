"""Settings come from the environment, and the real model is built only on demand."""

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.agent
from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from tests.fakes import ScriptedChatModel


def test_defaults_apply_when_nothing_is_set() -> None:
    assert load_settings({}) == Settings(
        model_name="anthropic:claude-sonnet-5-5", blocked_topics=("weapons", "malware"), max_tool_calls=5
    )


def test_values_are_read_from_the_environment() -> None:
    environ = {"AGENT_MODEL": "openai:gpt-test", "BLOCKED_TOPICS": " a , b c,, ", "MAX_TOOL_CALLS": "3"}

    assert load_settings(environ) == Settings(model_name="openai:gpt-test", blocked_topics=("a", "b c"), max_tool_calls=3)


def test_empty_topic_list_disables_the_blocklist() -> None:
    assert load_settings({"BLOCKED_TOPICS": ""}).blocked_topics == ()


@pytest.mark.parametrize("raw", ["0", "-1", "five", "2.5"])
def test_invalid_tool_call_limit_is_refused(raw: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"MAX_TOOL_CALLS": raw})


IMPORT_PROBE = """
import langchain.chat_models

def refuse(*args, **kwargs):
    raise SystemExit("a model was built at import time")

langchain.chat_models.init_chat_model = refuse
import app.main
"""


def test_importing_the_app_builds_no_model() -> None:
    result = subprocess.run([sys.executable, "-c", IMPORT_PROBE], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr


def test_configured_model_is_built_when_none_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    requested: list[str] = []
    fake = ScriptedChatModel(messages=iter([AIMessage(content="from the configured model")]))

    def fake_init_chat_model(name: str) -> ScriptedChatModel:
        requested.append(name)
        return fake

    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-test")
    monkeypatch.setattr(app.agent, "init_chat_model", fake_init_chat_model)

    # When
    response = TestClient(create_app()).post("/chat", json={"session_id": "s1", "message": "hi"})

    # Then
    assert requested == ["anthropic:claude-test"]
    assert response.json()["reply"] == "from the configured model"


def test_admin_key_is_read_and_kept_out_of_repr() -> None:
    settings = load_settings({"ADMIN_API_KEY": "s3cret-value"})

    assert settings.admin_api_key == "s3cret-value"
    assert "s3cret-value" not in repr(settings)


@pytest.mark.parametrize("environ", [{}, {"ADMIN_API_KEY": ""}])
def test_missing_or_empty_admin_key_means_none(environ: dict[str, str]) -> None:
    assert load_settings(environ).admin_api_key is None
