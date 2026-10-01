"""The deep agent and the guarded chat turn built around it."""

from collections import defaultdict
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from functools import lru_cache, partial

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.conversations import ASSISTANT, USER, ConversationKey, ConversationMessage, ConversationStore
from app.guardrails import (
    PII_REDACTION,
    REDACTED,
    STOPPED,
    TOOL_CALL_LIMIT,
    GuardrailAction,
    Redaction,
    StreamRedactor,
    TopicBlocklist,
    redact_pii,
)
from app.policies import GuardrailPolicy
from app.tools import list_custom_tools

SYSTEM_PROMPT = (
    "You are a helpful assistant. Use the calculator tool for arithmetic and the "
    "current_utc_time tool when the date or time matters. Placeholders such as "
    "[REDACTED_EMAIL] and [REDACTED_PHONE] stand for personal data the user shared; "
    "never ask for the original values."
)
LIMIT_REACHED_REPLY = (
    "I stopped working on this request because it reached the limit of {limit} tool "
    "calls per turn. Please narrow the request and try again."
)
EMPTY_REPLY = "I do not have a reply for that."
TENANT_INSTRUCTIONS_HEADING = "Additional instructions for this deployment:"
# Each distinct (tool-call limit, system prompt) pair compiles its own agent;
# tenants sharing a policy shape share one, and the least used are dropped.
AGENT_CACHE_SIZE = 128


class TopicBlockedError(Exception):
    """The message mentions a blocked topic, so the model was never called."""


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """The reply to one message, redacted if the policy asks, and the guardrails that acted on it."""

    reply: str
    guardrails: list[GuardrailAction]


@dataclass(frozen=True, slots=True)
class ReplyChunk:
    """A piece of a streamed reply, already redacted if the policy asks."""

    text: str


def build_model(settings: Settings) -> BaseChatModel:
    """Create the real chat model named by configuration."""
    return init_chat_model(settings.model_name)


def build_agent(*, model: BaseChatModel, max_tool_calls: int, tenant_prompt: str | None = None) -> CompiledStateGraph:
    """Assemble the deep agent with our tools and a per-run tool-call budget.

    ``exit_behavior="error"`` aborts the run as soon as the model asks for a
    call past the budget, so no further tool runs and the caller decides what
    to tell the user. A ``tenant_prompt`` is appended to our own instructions,
    never substituted for them.
    """
    return create_deep_agent(
        model=model,
        tools=list_custom_tools(),
        system_prompt=compose_system_prompt(tenant_prompt),
        middleware=[ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error")],
    )


def compose_system_prompt(tenant_prompt: str | None) -> str:
    """Our instructions, followed by the tenant's when it has any."""
    if tenant_prompt is None or not tenant_prompt.strip():
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{TENANT_INSTRUCTIONS_HEADING}\n{tenant_prompt}"


def redact_if(text: str, *, enabled: bool) -> Redaction:
    """Redact PII when the policy asks for it, else pass the text through untouched."""
    return redact_pii(text) if enabled else Redaction(text=text, changed=False)


class GuardedChat:
    """Runs chat turns through the blocklist, PII redaction, conversation memory and the agent.

    The guardrails come from the policy passed with each turn. The model sees
    the conversation's earlier turns as they were stored, redacted again when
    the policy redacts now. A turn is stored once its reply is complete; a
    refused message never is.
    """

    def __init__(self, *, model: BaseChatModel, conversations: ConversationStore) -> None:
        self._agent_for = lru_cache(maxsize=AGENT_CACHE_SIZE)(partial(build_agent, model=model))
        self._conversations = conversations

    async def run_turn(self, message: str, *, conversation: ConversationKey, policy: GuardrailPolicy) -> ChatTurn:
        """Answer one message, or raise ``TopicBlockedError`` before any model call."""
        async for event in self.stream_turn(message, conversation=conversation, policy=policy):
            if isinstance(event, ChatTurn):
                return event
        raise RuntimeError("a turn stream ended without its ChatTurn")

    def stream_turn(
        self, message: str, *, conversation: ConversationKey, policy: GuardrailPolicy
    ) -> AsyncIterator[ReplyChunk | ChatTurn]:
        """Answer one message as ``ReplyChunk`` events followed by the ``ChatTurn``.

        Raises ``TopicBlockedError`` at once, before anything streams, when the
        message is refused. The chunks join into exactly ``ChatTurn.reply``.
        """
        if TopicBlocklist(policy.blocked_topics).is_blocked(message):
            raise TopicBlockedError
        return self._produce_turn(message=message, conversation=conversation, policy=policy)

    async def _produce_turn(
        self, *, message: str, conversation: ConversationKey, policy: GuardrailPolicy
    ) -> AsyncIterator[ReplyChunk | ChatTurn]:
        request = redact_if(message, enabled=policy.redact_pii)
        history = await self._conversations.list_messages(conversation) or ()
        prompt = [*rebuild_history(history, redact=policy.redact_pii), HumanMessage(content=request.text)]
        agent = self._agent_for(max_tool_calls=policy.max_tool_calls, tenant_prompt=policy.system_prompt)
        redactor = StreamRedactor(enabled=policy.redact_pii)
        guardrails: list[GuardrailAction] = []
        reply: list[str] = []
        try:
            async for piece in stream_final_reply(agent=agent, messages=prompt):
                if released := redactor.push(piece):
                    reply.append(released)
                    yield ReplyChunk(text=released)
        except ToolCallLimitExceededError:
            if limit_reply := redactor.push(LIMIT_REACHED_REPLY.format(limit=policy.max_tool_calls)):
                reply.append(limit_reply)
                yield ReplyChunk(text=limit_reply)
            guardrails.append(GuardrailAction(name=TOOL_CALL_LIMIT, action=STOPPED))
        if tail := redactor.flush():
            reply.append(tail)
            yield ReplyChunk(text=tail)
        if request.changed or redactor.changed:
            guardrails.insert(0, GuardrailAction(name=PII_REDACTION, action=REDACTED))
        turn = ChatTurn(reply="".join(reply), guardrails=guardrails)
        await self._conversations.append_messages(
            key=conversation,
            messages=[
                ConversationMessage(role=USER, content=request.text),
                ConversationMessage(role=ASSISTANT, content=turn.reply),
            ],
        )
        yield turn


def rebuild_history(history: Iterable[ConversationMessage], *, redact: bool) -> list[BaseMessage]:
    """Turn stored messages back into model messages.

    They were redacted when stored if the policy redacted then; redacting again
    covers a tenant that has switched redaction on since.
    """
    return [
        (HumanMessage if stored.role == USER else AIMessage)(content=redact_if(stored.content, enabled=redact).text)
        for stored in history
    ]


async def stream_final_reply(*, agent: CompiledStateGraph, messages: list[BaseMessage]) -> AsyncIterator[str]:
    """Run the agent and yield the text of its final answer in the chunks the model streamed.

    The model streams every call it makes, but only a call that ends without
    tool calls is the answer; text from a call that goes on to use a tool is a
    preamble the caller never sees. A call's chunks are therefore held until the
    call completes and released only if it was the answer. Should the chunks
    ever disagree with the completed message, the message wins.
    """
    pending: dict[str, list[str]] = defaultdict(list)
    answered = False
    async for mode, payload in agent.astream({"messages": messages}, stream_mode=["messages", "updates"]):
        if mode == "messages":
            collect_chunk(chunk=payload[0], pending=pending)
            continue
        for answer in list_completed_answers(payload):
            chunks = pending.pop(answer.id or "", [])
            texts = chunks if "".join(chunks) == answer.text else [answer.text]
            for text in filter(None, texts):
                answered = True
                yield text
    if not answered:
        yield EMPTY_REPLY


def collect_chunk(*, chunk: BaseMessage, pending: dict[str, list[str]]) -> None:
    """Hold a streamed text chunk under the id of the model call it belongs to."""
    if isinstance(chunk, AIMessageChunk) and chunk.id and chunk.text:
        pending[chunk.id].append(chunk.text)


def list_completed_answers(updates: object) -> list[AIMessage]:
    """Pick out, from one ``updates`` stream event, the model messages that make no tool calls."""
    if not isinstance(updates, dict):
        return []
    answers: list[AIMessage] = []
    for update in updates.values():
        messages = update.get("messages", []) if isinstance(update, dict) else []
        answers.extend(
            message
            for message in (messages if isinstance(messages, list) else [messages])
            if isinstance(message, AIMessage) and not message.tool_calls
        )
    return answers
