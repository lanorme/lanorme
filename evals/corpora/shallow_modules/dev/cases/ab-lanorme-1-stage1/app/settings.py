"""Service configuration, read from ``AGENT_*`` environment variables."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    ``AGENT_BLOCKED_TOPICS`` is a comma-separated list, for example
    ``AGENT_BLOCKED_TOPICS="weapons,malware,gambling"``.
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    model: str = "anthropic:claude-sonnet-5-5"
    blocked_topics: Annotated[tuple[str, ...], NoDecode] = ("weapons", "malware")
    tool_call_limit: int = Field(default=5, ge=0)

    @field_validator("blocked_topics", mode="before")
    @classmethod
    def split_topics(cls, value: object) -> object:
        """Accept a comma-separated string as well as a sequence."""
        if isinstance(value, str):
            return tuple(topic.strip() for topic in value.split(",") if topic.strip())
        return value
