"""Service configuration, read from environment variables."""

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    """Environment-driven settings.

    AGENT_MODEL is a provider-prefixed name for init_chat_model; BLOCKED_TOPICS is
    comma-separated; MAX_TOOL_CALLS caps tool calls per turn. Those two make up the
    default policy for tenants without one. ADMIN_API_KEY guards the tenant policy
    endpoints; when it is unset or empty they refuse every request.
    """

    agent_model: str = "anthropic:claude-sonnet-5-5"
    blocked_topics: Annotated[tuple[str, ...], NoDecode] = ("weapons", "malware")
    max_tool_calls: int = Field(default=5, ge=0)
    admin_api_key: SecretStr | None = None

    @field_validator("blocked_topics", mode="before")
    @classmethod
    def split_topics(cls, value: object) -> object:
        """Accept the comma-separated form that environment variables use."""
        if isinstance(value, str):
            return tuple(topic.strip() for topic in value.split(",") if topic.strip())
        return value
