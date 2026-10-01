"""Port for the conversational agent the chat service drives."""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from app.domain.conversation import ChatMessage

# Joins the texts of separate model messages within one reply.
PARAGRAPH_BREAK = "\n\n"


class ChatAgent(Protocol):
    """An agent that answers one user message given the conversation so far."""

    def stream_reply(
        self,
        message: str,
        *,
        history: Sequence[ChatMessage],
        max_tool_calls: int,
        system_prompt: str | None,
    ) -> AsyncIterator[str]:
        """Yield the reply's text in chunks, adding system_prompt (when set) to the instructions.

        history holds the earlier turns, oldest first. Text from separate model
        messages in the turn is joined with PARAGRAPH_BREAK. Raises
        ToolCallLimitError when the turn goes past max_tool_calls, and
        AgentUnavailableError when the underlying model fails.
        """
        ...
