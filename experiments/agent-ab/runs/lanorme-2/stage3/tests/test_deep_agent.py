import asyncio
from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.domain.conversation import ChatMessage, Role
from app.domain.errors import AgentUnavailableError, ToolCallLimitError
from app.infrastructure.agent_tools import AGENT_TOOLS
from app.infrastructure.services.deep_agent import (
    SYSTEM_PROMPT,
    TENANT_PROMPT_HEADING,
    DeepAgentChat,
    compose_instructions,
    find_reply_writer,
    to_langchain_message,
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
        return "".join(await self.stream(message))

    async def stream(self, message: str, history: Sequence[ChatMessage] = ()) -> list[str]:
        return await collect_chunks(
            self.inner,
            message,
            history=history,
            max_tool_calls=self._max_tool_calls,
            system_prompt=self._system_prompt,
        )


async def collect_chunks(
    agent: DeepAgentChat,
    message: str,
    *,
    history: Sequence[ChatMessage] = (),
    max_tool_calls: int,
    system_prompt: str | None,
) -> list[str]:
    stream = agent.stream_reply(
        message, history=history, max_tool_calls=max_tool_calls, system_prompt=system_prompt
    )
    return [chunk async for chunk in stream]


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
    first = asyncio.run(collect_chunks(agent, "a", max_tool_calls=2, system_prompt="Tenant A."))
    with pytest.raises(ToolCallLimitError) as caught:
        asyncio.run(collect_chunks(agent, "b", max_tool_calls=1, system_prompt=None))

    # Then
    assert "".join(first) == "first"
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


def test_reply_streams_in_the_models_chunks() -> None:
    model = build_scripted_model(AIMessage(content="It is noon."))

    chunks = asyncio.run(build_agent(model).stream("time?"))

    assert chunks == ["It", " ", "is", " ", "noon."]


def test_history_is_sent_before_the_new_message() -> None:
    # Given
    model = build_scripted_model(AIMessage(content="Your name is Ada."))
    history = [
        ChatMessage(role=Role.USER, content="I am Ada."),
        ChatMessage(role=Role.ASSISTANT, content="Hello Ada."),
    ]

    # When
    asyncio.run(build_agent(model).stream("Who am I?", history))

    # Then
    conversation = [(msg.type, msg.text) for msg in model.calls[0] if msg.type != "system"]
    assert conversation == [("human", "I am Ada."), ("ai", "Hello Ada."), ("human", "Who am I?")]


def test_text_beside_a_tool_call_is_kept_and_messages_are_separated() -> None:
    # Given
    model = build_scripted_model(
        build_tool_call("calculator", {"expression": "2 + 2"}, "calc-1", text="Let me add."),
        AIMessage(content="It is 4."),
    )

    # When
    chunks = asyncio.run(build_agent(model).stream("2 + 2?"))

    # Then
    assert "".join(chunks) == "Let me add.\n\nIt is 4."


def test_tool_results_are_not_part_of_the_reply() -> None:
    # Given
    model = build_scripted_model(
        build_tool_call("calculator", {"expression": "6 * 7"}, "calc-1"),
        AIMessage(content="Done."),
    )

    # When
    chunks = asyncio.run(build_agent(model).stream("6 * 7?"))

    # Then
    assert "".join(chunks) == "Done."


def test_text_before_a_call_past_the_limit_is_streamed_before_the_error() -> None:
    # Given
    model = build_scripted_model(build_tool_call("current_time", {}, "c-1", text="Checking."))
    stream = DeepAgentChat(model=model, tools=AGENT_TOOLS).stream_reply(
        "time?", history=(), max_tool_calls=0, system_prompt=None
    )

    async def drain() -> list[str]:
        received: list[str] = []
        with pytest.raises(ToolCallLimitError):
            async for chunk in stream:
                received.append(chunk)
        return received

    # When
    received = asyncio.run(drain())

    # Then
    assert "".join(received) == "Checking."


@pytest.mark.parametrize(
    ("message", "expected_type"),
    [(ChatMessage(role=Role.USER, content="hi"), "human"), (ChatMessage(role=Role.ASSISTANT, content="hi"), "ai")],
)
def test_stored_roles_map_to_langchain_messages(message: ChatMessage, expected_type: str) -> None:
    converted = to_langchain_message(message)

    assert (converted.type, converted.text) == (expected_type, "hi")


@pytest.mark.parametrize(
    ("chunk", "metadata"),
    [
        (AIMessage(content="x"), {"langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:1"}),
        (AIMessage(content="x"), {"langgraph_node": "model", "langgraph_checkpoint_ns": "tools:1|model:2"}),
        (AIMessage(content="x"), {"langgraph_node": "model"}),
        (HumanMessage(content="x"), {"langgraph_node": "model", "langgraph_checkpoint_ns": "model:1"}),
    ],
)
def test_only_the_agents_own_model_writes_the_reply(
    chunk: AIMessage | HumanMessage, metadata: dict[str, str]
) -> None:
    assert find_reply_writer(chunk, metadata=metadata) is None


def test_top_level_model_call_is_named_by_its_namespace() -> None:
    metadata = {"langgraph_node": "model", "langgraph_checkpoint_ns": "model:abc"}

    assert find_reply_writer(AIMessage(content="x"), metadata=metadata) == "model:abc"


def test_subagent_text_is_not_part_of_the_reply() -> None:
    # Given
    model = build_scripted_model(
        build_tool_call(
            "task",
            {"description": "Find the answer.", "subagent_type": "general-purpose"},
            "task-1",
        ),
        AIMessage(content="Subagent findings."),
        AIMessage(content="Main answer."),
    )

    # When
    chunks = asyncio.run(build_agent(model).stream("research this"))

    # Then
    assert len(model.calls) == 3
    assert "".join(chunks) == "Main answer."
