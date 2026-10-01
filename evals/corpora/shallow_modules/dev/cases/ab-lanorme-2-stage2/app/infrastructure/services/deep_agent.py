"""ChatAgent adapter built on deepagents' create_deep_agent."""

import logging
from collections.abc import Sequence
from functools import lru_cache

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.application.ports.chat_agent import ChatAgent
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


class DeepAgentChat(ChatAgent):
    """A deep agent run once per message, with no checkpointer so turns stay independent."""

    def __init__(self, *, model: BaseChatModel, tools: Sequence[BaseTool]) -> None:
        self._model = model
        self._tools = list(tools)
        self._graph_for = lru_cache(maxsize=_GRAPH_CACHE_SIZE)(self._build_graph)

    async def reply(self, message: str, *, max_tool_calls: int, system_prompt: str | None) -> str:
        """Run the agent on one message and return the text of its final message."""
        graph = self._graph_for(max_tool_calls=max_tool_calls, system_prompt=system_prompt)
        try:
            state = await graph.ainvoke({"messages": [HumanMessage(content=message)]})
        except ToolCallLimitExceededError as exc:
            raise ToolCallLimitError(limit=max_tool_calls) from exc
        except Exception as exc:
            logger.exception("agent run failed")
            raise AgentUnavailableError from exc
        return state["messages"][-1].text

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
