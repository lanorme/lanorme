"""Fake chat models for driving the agent without a real LLM."""

import itertools
from collections.abc import Callable, Iterator, Sequence

from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import Field


class ScriptedChatModel(GenericFakeChatModel):
    """A fake chat model that replays scripted messages and records its inputs."""

    received: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[object], **kwargs: object) -> "ScriptedChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @property
    def last_user_text(self) -> str:
        return next(message.text for message in reversed(self.received[-1]) if message.type == "human")


def make_tool_call(name: str, args: dict[str, str], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def repeat_calculator_calls() -> Iterator[AIMessage]:
    """A model that asks for one more calculation on every step, forever."""
    for step in itertools.count():
        yield make_tool_call("calculator", {"expression": f"{step} + 1"}, f"call_{step}")


type ClientFactory = Callable[["ScriptedChatModel"], TestClient]

ADMIN_KEY = "test-admin-key"
DEFAULT_POLICY = {"blocked_topics": ["weapons", "malware"], "redact_pii": True, "max_tool_calls": 5, "system_prompt": None}


def make_policy(**overrides: object) -> dict[str, object]:
    """A full policy body: the default policy with some fields changed."""
    return DEFAULT_POLICY | overrides


def put_policy(*, client: TestClient, tenant_id: str, policy: dict[str, object]) -> None:
    """Store a tenant's policy through the admin API, failing loudly if it is refused."""
    response = client.put(f"/tenants/{tenant_id}/policy", json=policy, headers={"X-Admin-Key": ADMIN_KEY})
    assert response.status_code == 200, response.text


def find_system_text(model: ScriptedChatModel) -> str:
    """The system instructions the model saw on its most recent call."""
    return next(message.text for message in model.received[-1] if message.type == "system")
