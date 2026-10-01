"""Service configuration, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
DEFAULT_BLOCKED_TOPICS = ("weapons", "malware")
DEFAULT_MAX_TOOL_CALLS = 5
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Use the calculator tool for arithmetic and "
    "the current_time tool when you need the date or time. Be concise."
)


@dataclass(frozen=True)
class Settings:
    model: str = DEFAULT_MODEL
    blocked_topics: tuple[str, ...] = DEFAULT_BLOCKED_TOPICS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Key for the tenant policy endpoints. None (or empty) means they always return 401.
    admin_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the environment.

        - `AGENT_MODEL`: `provider:model` string for `init_chat_model`.
        - `BLOCKED_TOPICS`: comma-separated list; an empty value disables the blocklist.
        - `MAX_TOOL_CALLS`: non-negative integer, tool calls allowed per turn.
        - `AGENT_SYSTEM_PROMPT`: system prompt for the agent.
        - `ADMIN_API_KEY`: key required by the tenant policy endpoints.
        """
        env = os.environ if env is None else env

        blocked = DEFAULT_BLOCKED_TOPICS
        if "BLOCKED_TOPICS" in env:
            blocked = tuple(t.strip() for t in env["BLOCKED_TOPICS"].split(",") if t.strip())

        max_tool_calls = DEFAULT_MAX_TOOL_CALLS
        if raw := env.get("MAX_TOOL_CALLS", "").strip():
            try:
                max_tool_calls = int(raw)
            except ValueError:
                raise ValueError(f"MAX_TOOL_CALLS must be an integer, got {raw!r}") from None
            if max_tool_calls < 0:
                raise ValueError(f"MAX_TOOL_CALLS must be >= 0, got {max_tool_calls}")

        return cls(
            model=env.get("AGENT_MODEL", "").strip() or DEFAULT_MODEL,
            blocked_topics=blocked,
            max_tool_calls=max_tool_calls,
            system_prompt=env.get("AGENT_SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT,
            admin_api_key=env.get("ADMIN_API_KEY") or None,
        )
