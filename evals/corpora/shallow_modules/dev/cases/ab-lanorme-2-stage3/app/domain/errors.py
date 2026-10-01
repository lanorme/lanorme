"""Failures a chat turn can end in, independent of transport and agent library."""


class TopicBlockedError(Exception):
    """The user's message mentions a blocked topic, so the model must not see it."""

    def __init__(self, *, topic: str) -> None:
        super().__init__(f"message mentions blocked topic {topic!r}")
        self.topic = topic


class ToolCallLimitError(Exception):
    """The agent tried to make more tool calls in one turn than the limit allows."""

    def __init__(self, *, limit: int) -> None:
        super().__init__(f"agent exceeded the limit of {limit} tool calls")
        self.limit = limit


class AgentUnavailableError(Exception):
    """The agent could not produce a reply (model or provider failure)."""


class SessionNotFoundError(Exception):
    """The tenant has no conversation under this session ID."""

    def __init__(self, *, session_id: str) -> None:
        super().__init__(f"no conversation for session {session_id!r}")
        self.session_id = session_id
