"""Service configuration, read from environment variables."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """All settings use the ``AGENT_`` prefix, e.g. ``AGENT_MODEL``."""

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    # Passed to ``init_chat_model``; ``provider:model`` form.
    model: str = "anthropic:claude-sonnet-5-5"
    # Comma-separated in the environment: AGENT_BLOCKED_TOPICS="weapons,malware".
    blocked_topics: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["weapons", "malware"]
    )
    max_tool_calls: int = Field(default=5, ge=0)
    system_prompt: str = (
        "You are a helpful assistant. Use the calculator tool for arithmetic "
        "and the current_time tool when asked about the date or time."
    )

    @field_validator("blocked_topics", mode="before")
    @classmethod
    def _split_topics(cls, value: object) -> object:
        if isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
