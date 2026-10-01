import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.domain.errors import AgentUnavailableError, ToolCallLimitError
from app.infrastructure.agent_tools import AGENT_TOOLS
from app.infrastructure.services.deep_agent import (
    SYSTEM_PROMPT,
    TENANT_PROMPT_HEADING,
    DeepAgentChat,
    compose_instructions,
)
from tests.fakes import ScriptedChatModel, build_clock_calls, build_scripted_model, build_tool_call


class BoundAgent:
    """A DeepAgentChat with one fixed limit and prompt, to keep the tests short."""

    def __init__(
        self, model: ScriptedChatModel, *, max_tool_calls: int, system_prompt: str | None
    ) -> None:
        self.inner = DeepAgentChat(model=model, tools=AGENT_TOOLS)
        self._max_tool_calls = max_tool_calls
        self._system_prompt = system_prompt

    async def reply(self, message: str) -> str:
        return await self.inner.reply(
            message, max_tool_calls=self._max_tool_calls, system_prompt=self._system_prompt
        )


def build_agent(
    model: ScriptedChatModel, *, max_tool_calls: int = 5, system_prompt: str | None = None
) -> BoundAgent:
    return BoundAgent(model, max_tool_calls=max_tool_calls, system_prompt=system_prompt)


def read_system_text(model: ScriptedChatModel, call: int = 0) -> str:
    return next(msg.text for msg in model.calls[call] if msg.type == "system")


def test_runs_custom_tool_and_returns_final_text() -> None:
    # Given
    model = build_scripted_model(
        build_tool_call("calculator", {"expression": "6 * 7"}, "calc-1"),
        AIMessage(content="The answer is 42."),
    )

    # When
    reply = asyncio.run(build_agent(model).reply("what is 6 * 7?"))

    # Then
    assert reply == "The answer is 42."
    tool_results = [msg for msg in model.calls[-1] if isinstance(msg, ToolMessage)]
    assert [msg.content for msg in tool_results] == ["42"]


def test_turns_are_independent() -> None:
    # Given
    model = build_scripted_model(AIMessage(content="first"), AIMessage(content="second"))
    agent = build_agent(model)

    # When
    asyncio.run(agent.reply("one"))
    asyncio.run(agent.reply("two"))

    # Then
    second_turn_text = [msg.text for msg in model.calls[1] if msg.type == "human"]
    assert second_turn_text == ["two"]


def test_calls_up_to_the_limit_are_allowed() -> None:
    model = build_scripted_model(*build_clock_calls(2), AIMessage(content="done"))

    assert asyncio.run(build_agent(model, max_tool_calls=2).reply("time?")) == "done"


def test_going_past_the_limit_raises_before_the_extra_call_runs() -> None:
    # Given
    model = build_scripted_model(*build_clock_calls(3), AIMessage(content="never"))

    # When
    with pytest.raises(ToolCallLimitError) as caught:
        asyncio.run(build_agent(model, max_tool_calls=2).reply("time?"))

    # Then
    assert caught.value.limit == 2
    assert len(model.calls) == 3


def test_parallel_calls_in_one_message_count_individually() -> None:
    batch = AIMessage(
        content="",
        tool_calls=[
            {"name": "current_time", "args": {}, "id": f"batch-{index}"} for index in range(3)
        ],
    )
    model = build_scripted_model(batch, AIMessage(content="never"))

    with pytest.raises(ToolCallLimitError):
        asyncio.run(build_agent(model, max_tool_calls=2).reply("time?"))


def test_model_failure_becomes_agent_unavailable() -> None:
    model = build_scripted_model()

    with pytest.raises(AgentUnavailableError):
        asyncio.run(build_agent(model).reply("hello"))


def test_tenant_prompt_is_added_after_the_base_instructions() -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Ahoy."))

    # When
    asyncio.run(build_agent(model, system_prompt="Answer like a pirate.").reply("hi"))

    # Then
    text = read_system_text(model)
    assert SYSTEM_PROMPT in text
    assert f"{TENANT_PROMPT_HEADING}\nAnswer like a pirate." in text
    assert text.index(SYSTEM_PROMPT) < text.index("Answer like a pirate.")


def test_without_tenant_prompt_only_base_instructions_are_used() -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Hi."))

    # When
    asyncio.run(build_agent(model).reply("hi"))

    # Then
    assert SYSTEM_PROMPT in read_system_text(model)
    assert TENANT_PROMPT_HEADING not in read_system_text(model)


def test_limit_and_prompt_vary_per_call_on_one_agent() -> None:
    # Given
    model = build_scripted_model(
        *build_clock_calls(2), AIMessage(content="first"), *build_clock_calls(2)
    )
    agent = DeepAgentChat(model=model, tools=AGENT_TOOLS)

    # When
    first = asyncio.run(agent.reply("a", max_tool_calls=2, system_prompt="Tenant A."))
    with pytest.raises(ToolCallLimitError) as caught:
        asyncio.run(agent.reply("b", max_tool_calls=1, system_prompt=None))

    # Then
    assert first == "first"
    assert caught.value.limit == 1
    assert "Tenant A." in read_system_text(model, call=0)
    assert "Tenant A." not in read_system_text(model, call=3)


def test_same_configuration_reuses_the_compiled_graph() -> None:
    # Given
    model = build_scripted_model(AIMessage(content="one"), AIMessage(content="two"))
    agent = build_agent(model, max_tool_calls=3, system_prompt="Be brief.")

    # When
    asyncio.run(agent.reply("x"))
    asyncio.run(agent.reply("y"))

    # Then
    cache = agent.inner._graph_for.cache_info()
    assert (cache.hits, cache.misses) == (1, 1)


@pytest.mark.parametrize("prompt", [None, "", "   "])
def test_blank_tenant_prompt_adds_nothing(prompt: str | None) -> None:
    assert compose_instructions(prompt) == SYSTEM_PROMPT
