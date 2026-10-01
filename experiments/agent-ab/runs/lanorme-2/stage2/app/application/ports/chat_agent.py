"""Port for the conversational agent the chat service drives."""

from typing import Protocol


class ChatAgent(Protocol):
    """An agent that answers one user message with no memory of earlier turns."""

    async def reply(self, message: str, *, max_tool_calls: int, system_prompt: str | None) -> str:
        """Answer the message, adding system_prompt (when set) to the agent's instructions.

        Raises ToolCallLimitError when the turn goes past max_tool_calls, and
        AgentUnavailableError when the underlying model fails.
        """
        ...
