"""ChatAgent adapter built on deepagents' create_deep_agent."""

import logging
from collections.abc import Sequence

from deepagents import create_deep_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from app.application.ports.chat_agent import ChatAgent
from app.domain.errors import AgentUnavailableError, ToolCallLimitError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a helpful assistant. Use the calculator tool for arithmetic and the "
    "current_time tool for dates and times. Personal details in the user's message "
    "may appear as [REDACTED_EMAIL] or [REDACTED_PHONE]; never try to recover them."
)


class DeepAgentChat(ChatAgent):
    """A deep agent run once per message, with no checkpointer so turns stay independent."""

    def __init__(
        self, *, model: BaseChatModel, tools: Sequence[BaseTool], max_tool_calls: int
    ) -> None:
        self._max_tool_calls = max_tool_calls
        # exit_behavior="error" aborts before any call past the limit executes.
        limiter = ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="error")
        self._graph = create_deep_agent(
            model=model, tools=list(tools), system_prompt=SYSTEM_PROMPT, middleware=[limiter]
        )

    async def reply(self, message: str) -> str:
        """Run the agent on one message and return the text of its final message."""
        try:
            state = await self._graph.ainvoke({"messages": [HumanMessage(content=message)]})
        except ToolCallLimitExceededError as exc:
            raise ToolCallLimitError(limit=self._max_tool_calls) from exc
        except Exception as exc:
            logger.exception("agent run failed")
            raise AgentUnavailableError from exc
        return state["messages"][-1].text
