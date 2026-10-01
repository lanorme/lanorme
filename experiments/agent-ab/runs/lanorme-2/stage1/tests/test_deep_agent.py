import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.domain.errors import AgentUnavailableError, ToolCallLimitError
from app.infrastructure.agent_tools import AGENT_TOOLS
from app.infrastructure.services.deep_agent import DeepAgentChat
from tests.fakes import ScriptedChatModel, build_clock_calls, build_scripted_model, build_tool_call


def build_agent(model: ScriptedChatModel, *, max_tool_calls: int = 5) -> DeepAgentChat:
    return DeepAgentChat(model=model, tools=AGENT_TOOLS, max_tool_calls=max_tool_calls)


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
