"""Port for the conversational agent the chat service drives."""

from typing import Protocol


class ChatAgent(Protocol):
    """An agent that answers one user message with no memory of earlier turns."""

    async def reply(self, message: str) -> str:
        """Answer the message.

        Raises ToolCallLimitError when the turn goes past its tool-call limit, and
        AgentUnavailableError when the underlying model fails.
        """
        ...
