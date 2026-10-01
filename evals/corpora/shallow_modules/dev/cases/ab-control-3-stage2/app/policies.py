"""Per-tenant guardrail policies and where they are kept.

`PolicyStore` is the persistence boundary: the app only talks to that
protocol, so the in-memory store can be swapped for a database-backed one
(set `app.state.policy_store`) without touching the request handlers.
"""

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings

DEFAULT_TENANT = "default"

MAX_TOPICS = 100
MAX_TOPIC_LENGTH = 100
MAX_TOOL_CALLS = 100
MAX_SYSTEM_PROMPT_LENGTH = 8000


class Policy(BaseModel):
    """A tenant's guardrail policy. Every field is required in a request body.

    Validation is strict (no "5" for 5, no 1 for true) and unknown fields are
    rejected, so a typo in a policy is a 422 rather than a silently ignored
    setting. Topics are stripped, blanks dropped and case-insensitive
    duplicates removed; a blank system prompt is stored as null.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    blocked_topics: Annotated[list[str], Field(max_length=MAX_TOPICS)]
    redact_pii: bool
    max_tool_calls: Annotated[int, Field(ge=0, le=MAX_TOOL_CALLS)]
    system_prompt: Annotated[str | None, Field(max_length=MAX_SYSTEM_PROMPT_LENGTH)]

    @field_validator("blocked_topics")
    @classmethod
    def _normalise_topics(cls, topics: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for topic in (t.strip() for t in topics):
            if len(topic) > MAX_TOPIC_LENGTH:
                raise ValueError(f"topics must be at most {MAX_TOPIC_LENGTH} characters")
            if topic:
                seen.setdefault(topic.casefold(), topic)
        return list(seen.values())

    @field_validator("system_prompt")
    @classmethod
    def _normalise_system_prompt(cls, prompt: str | None) -> str | None:
        if prompt is None or not prompt.strip():
            return None
        return prompt.strip()


def default_policy(settings: Settings) -> Policy:
    """The policy for tenants without a stored one.

    Built from the service settings (whose defaults are weapons/malware and
    5 tool calls) so the stage-one environment variables still apply. Uses
    `model_construct` because env values were validated by `Settings` and are
    not bound by the API's request-size limits.
    """
    return Policy.model_construct(
        blocked_topics=list(settings.blocked_topics),
        redact_pii=True,
        max_tool_calls=settings.tool_call_limit,
        system_prompt=None,
    )


class PolicyStore(Protocol):
    """Storage for tenants' explicitly set policies."""

    async def get(self, tenant_id: str) -> Policy | None:
        """The stored policy, or None if the tenant has none."""
        ...

    async def put(self, tenant_id: str, policy: Policy) -> Policy:
        """Store (replace) the tenant's policy and return what was stored."""
        ...

    async def delete(self, tenant_id: str) -> None:
        """Remove the tenant's policy; a no-op if there is none."""
        ...


class InMemoryPolicyStore:
    """Process-local `PolicyStore`. Not shared between workers; lost on restart."""

    def __init__(self) -> None:
        self._policies: dict[str, Policy] = {}

    async def get(self, tenant_id: str) -> Policy | None:
        policy = self._policies.get(tenant_id)
        return policy.model_copy(deep=True) if policy else None

    async def put(self, tenant_id: str, policy: Policy) -> Policy:
        self._policies[tenant_id] = policy.model_copy(deep=True)
        return policy.model_copy(deep=True)

    async def delete(self, tenant_id: str) -> None:
        self._policies.pop(tenant_id, None)
