"""Service configuration, read from environment variables."""

import os
from dataclasses import dataclass, field

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
DEFAULT_BLOCKED_TOPICS = ("weapons", "malware")
DEFAULT_TOOL_CALL_LIMIT = 5


def _parse_topics(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_BLOCKED_TOPICS
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def _parse_limit(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_TOOL_CALL_LIMIT
    value = int(raw)
    if value < 0:
        raise ValueError(f"TOOL_CALL_LIMIT must be >= 0, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Runtime settings.

    Environment variables:
        AGENT_MODEL       `provider:model` string for `init_chat_model`
                          (only used when no model is injected).
        BLOCKED_TOPICS    Comma-separated topic list. Set to an empty string to
                          disable the blocklist. Default: "weapons,malware".
        TOOL_CALL_LIMIT   Max tool calls per turn. Default: 5.
    """

    model: str = DEFAULT_MODEL
    blocked_topics: tuple[str, ...] = DEFAULT_BLOCKED_TOPICS
    tool_call_limit: int = DEFAULT_TOOL_CALL_LIMIT
    system_prompt: str = field(
        default=(
            "You are a helpful assistant. Use the calculator tool for arithmetic "
            "and the current_time tool when asked about the date or time."
        )
    )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            model=os.environ.get("AGENT_MODEL") or DEFAULT_MODEL,
            blocked_topics=_parse_topics(os.environ.get("BLOCKED_TOPICS")),
            tool_call_limit=_parse_limit(os.environ.get("TOOL_CALL_LIMIT")),
        )
