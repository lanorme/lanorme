"""ChatAgent adapter built on deepagents' create_deep_agent."""

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from functools import lru_cache

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.application.ports.chat_agent import PARAGRAPH_BREAK, ChatAgent
from app.domain.conversation import ChatMessage, Role
from app.domain.errors import AgentUnavailableError, ToolCallLimitError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a helpful assistant. Use the calculator tool for arithmetic and the "
    "current_time tool for dates and times. Personal details in the user's message "
    "may appear as [REDACTED_EMAIL] or [REDACTED_PHONE]; never try to recover them."
)
TENANT_PROMPT_HEADING = "Additional instructions from the operator of this deployment:"

# One compiled graph per distinct (tool-call limit, tenant prompt). Compiling takes
# tens of milliseconds, and most tenants share a few configurations.
_GRAPH_CACHE_SIZE = 128

# The agent's own model node. Subagents run inside the "task" tool, so their
# model calls sit in a nested checkpoint namespace and are not part of the reply.
_MODEL_NODE = "model"
_NAMESPACE_SEPARATOR = "|"


class DeepAgentChat(ChatAgent):
    """A deep agent run once per message on the history it is given; it keeps no state itself."""

    def __init__(self, *, model: BaseChatModel, tools: Sequence[BaseTool]) -> None:
        self._model = model
        self._tools = list(tools)
        self._graph_for = lru_cache(maxsize=_GRAPH_CACHE_SIZE)(self._build_graph)

    async def stream_reply(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage],
        max_tool_calls: int,
        system_prompt: str | None,
    ) -> AsyncIterator[str]:
        """Run the agent on the conversation and yield the text its model writes, as it arrives.

        Each model call of the turn is one message; their texts are joined
        with PARAGRAPH_BREAK.
        """
        graph = self._graph_for(max_tool_calls=max_tool_calls, system_prompt=system_prompt)
        inputs = {"messages": [*map(to_langchain_message, history), HumanMessage(content=message)]}
        last_writer: str | None = None
        try:
            async for chunk, metadata in graph.astream(inputs, stream_mode="messages"):
                writer = find_reply_writer(chunk, metadata=metadata)
                text = chunk.text if writer is not None else ""
                if not text:
                    continue
                if last_writer is not None and writer != last_writer:
                    yield PARAGRAPH_BREAK
                last_writer = writer
                yield text
        except ToolCallLimitExceededError as exc:
            raise ToolCallLimitError(limit=max_tool_calls) from exc
        except Exception as exc:
            logger.exception("agent run failed")
            raise AgentUnavailableError from exc

    def _build_graph(self, *, max_tool_calls: int, system_prompt: str | None) -> CompiledStateGraph:
        # exit_behavior="error" aborts before any call past the limit executes.
        limiter = ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error")
        return create_deep_agent(
            model=self._model,
            tools=self._tools,
            system_prompt=compose_instructions(system_prompt),
            middleware=[limiter],
        )


def compose_instructions(tenant_prompt: str | None) -> str:
    """Return the base instructions, followed by the tenant's own when it has any."""
    if tenant_prompt is None or not tenant_prompt.strip():
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{TENANT_PROMPT_HEADING}\n{tenant_prompt}"


def to_langchain_message(message: ChatMessage) -> BaseMessage:
    """Turn a stored conversation message into the LangChain message the model reads."""
    if message.role is Role.USER:
        return HumanMessage(content=message.content)
    return AIMessage(content=message.content)


def find_reply_writer(chunk: BaseMessage, *, metadata: Mapping[str, object]) -> str | None:
    """Return the model call that wrote this chunk of the reply, or None when it is not reply text.

    Only the agent's own model node writes the reply; tool results and
    subagents' model calls do not. The call is named by its checkpoint namespace.
    """
    namespace = metadata.get("langgraph_checkpoint_ns")
    if (
        not isinstance(chunk, AIMessage)
        or metadata.get("langgraph_node") != _MODEL_NODE
        or not isinstance(namespace, str)
        or _NAMESPACE_SEPARATOR in namespace
    ):
        return None
    return namespace
