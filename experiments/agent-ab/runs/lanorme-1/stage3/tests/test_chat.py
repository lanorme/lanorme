import asyncio
from collections.abc import Iterator
from itertools import count

from langchain_core.messages import AIMessage

from app.agent import build_agent
from app.chat import ChatService
from app.conversations import InMemoryConversationStore, SessionKey
from app.policies import TenantPolicy
from tests.fakes import ScriptedChatModel, script, tool_call

KEY = SessionKey(tenant_id="acme", session_id="s-1")
POLICY = TenantPolicy(
    blocked_topics=("weapons",), redact_pii=True, max_tool_calls=50, system_prompt=None
)


def service_with(model: ScriptedChatModel) -> tuple[ChatService, InMemoryConversationStore]:
    store = InMemoryConversationStore()
    return ChatService(agent=build_agent(model=model), conversations=store), store


def narrating_calculator_calls() -> Iterator[AIMessage]:
    for index in count():
        yield AIMessage(
            content="Still adding. ",
            tool_calls=[tool_call("calculator", {"expression": "1 + 1"}, f"call-{index}")],
        )


def test_an_abandoned_stream_stores_nothing_and_stops_the_agent() -> None:
    # Given an agent that narrates while it keeps calling tools
    model = ScriptedChatModel(messages=narrating_calculator_calls())
    service, store = service_with(model)

    async def read_one_chunk_then_leave() -> tuple[int, int]:
        stream = await service.start(key=KEY, message="go", policy=POLICY)
        await anext(stream.chunks)
        await stream.chunks.aclose()
        on_leaving = model.calls
        await asyncio.sleep(0.1)
        return on_leaving, model.calls

    # When the reader leaves after the first chunk
    on_leaving, afterwards = asyncio.run(read_one_chunk_then_leave())

    # Then
    assert afterwards == on_leaving < POLICY.max_tool_calls
    assert asyncio.run(store.load(KEY)) is None


def test_chunks_can_be_read_from_different_tasks() -> None:
    # Given a streaming server may read each chunk from a fresh context
    service, store = service_with(script("one two three four", chunk_size=2))

    async def read_each_chunk_in_its_own_task() -> list[str]:
        stream = await service.start(key=KEY, message="hi", policy=POLICY)
        chunks = []
        while True:
            try:
                chunks.append(await asyncio.create_task(anext(stream.chunks)))
            except StopAsyncIteration:
                return chunks

    # When
    chunks = asyncio.run(read_each_chunk_in_its_own_task())

    # Then
    assert "".join(chunks) == "one two three four"
    assert len(asyncio.run(store.load(KEY))) == 2
