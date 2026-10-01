"""Service configuration, read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
DEFAULT_BLOCKED_TOPICS = ("weapons", "malware")
DEFAULT_MAX_TOOL_CALLS = 5


class ConfigError(ValueError):
    """An environment variable holds a value the service cannot use."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for the agent and its guardrails.

    ``model_name`` is any identifier ``init_chat_model`` accepts, such as
    ``"anthropic:claude-sonnet-5-5"``; it is only used when no model is
    injected into ``create_app``.
    """

    model_name: str = DEFAULT_MODEL
    blocked_topics: tuple[str, ...] = DEFAULT_BLOCKED_TOPICS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build settings from ``AGENT_MODEL``, ``BLOCKED_TOPICS`` and ``MAX_TOOL_CALLS``.

    ``BLOCKED_TOPICS`` is comma-separated; set it to an empty string to block
    nothing. Unset variables keep their defaults. Raises ``ConfigError`` when
    ``MAX_TOOL_CALLS`` is not a positive integer.
    """
    env = os.environ if environ is None else environ
    topics = env.get("BLOCKED_TOPICS")
    return Settings(
        model_name=env.get("AGENT_MODEL", DEFAULT_MODEL),
        blocked_topics=DEFAULT_BLOCKED_TOPICS if topics is None else split_topics(topics),
        max_tool_calls=parse_tool_call_limit(env.get("MAX_TOOL_CALLS")),
    )


def split_topics(raw: str) -> tuple[str, ...]:
    """Split a comma-separated topic list, dropping blanks and stray spaces."""
    return tuple(topic.strip() for topic in raw.split(",") if topic.strip())


def parse_tool_call_limit(raw: str | None) -> int:
    """Read the per-turn tool-call budget, which must be at least one."""
    if raw is None:
        return DEFAULT_MAX_TOOL_CALLS
    try:
        limit = int(raw)
    except ValueError as error:
        raise ConfigError(f"MAX_TOOL_CALLS must be an integer, got {raw!r}") from error
    if limit < 1:
        raise ConfigError(f"MAX_TOOL_CALLS must be at least 1, got {limit}")
    return limit
