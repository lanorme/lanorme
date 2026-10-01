"""Service configuration, read from ``AGENT_*`` environment variables and ``ADMIN_API_KEY``."""

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    ``AGENT_BLOCKED_TOPICS`` is a comma-separated list, for example
    ``AGENT_BLOCKED_TOPICS="weapons,malware,gambling"``. It and
    ``AGENT_TOOL_CALL_LIMIT`` shape the default tenant policy.
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    model: str = "anthropic:claude-sonnet-5-5"
    blocked_topics: Annotated[tuple[str, ...], NoDecode] = ("weapons", "malware")
    tool_call_limit: int = Field(default=5, ge=0)
    # Unprefixed by contract. Unset means no admin key is accepted at all.
    admin_api_key: SecretStr | None = Field(default=None, validation_alias="ADMIN_API_KEY")

    @field_validator("blocked_topics", mode="before")
    @classmethod
    def split_topics(cls, value: object) -> object:
        """Accept a comma-separated string as well as a sequence."""
        if isinstance(value, str):
            return tuple(topic.strip() for topic in value.split(",") if topic.strip())
        return value
