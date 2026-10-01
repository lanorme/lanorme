"""Per-tenant guardrail policies and where they are stored."""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from app.config import Settings

DEFAULT_TENANT = "default"


class TenantPolicy(BaseModel):
    """Guardrail settings for one tenant.

    Types are strict (no "yes" for a bool, no "5" for an int) and unknown fields
    are rejected, so a typo in a policy cannot silently weaken a guardrail.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocked_topics: list[StrictStr]
    redact_pii: StrictBool
    max_tool_calls: Annotated[StrictInt, Field(ge=0)]
    system_prompt: StrictStr | None

    @field_validator("blocked_topics")
    @classmethod
    def _no_blank_topics(cls, topics: list[str]) -> list[str]:
        if any(not t.strip() for t in topics):
            raise ValueError("blocked topics must not be blank")
        return topics

    @classmethod
    def default(cls, settings: Settings) -> TenantPolicy:
        """The policy for tenants without a stored one.

        With no environment overrides this is: blocked topics weapons and
        malware, PII redacted, 5 tool calls, no extra system prompt.
        `BLOCKED_TOPICS` and `MAX_TOOL_CALLS` still adjust it.
        """
        return cls(
            blocked_topics=list(settings.blocked_topics),
            redact_pii=True,
            max_tool_calls=settings.max_tool_calls,
            system_prompt=None,
        )


class PolicyStore(Protocol):
    """Storage for tenant policies. Async so a database-backed store can drop in."""

    async def get(self, tenant_id: str) -> TenantPolicy | None: ...

    async def put(self, tenant_id: str, policy: TenantPolicy) -> None: ...

    async def delete(self, tenant_id: str) -> None:
        """Remove the tenant's policy; a no-op if there is none."""
        ...


class InMemoryPolicyStore:
    def __init__(self) -> None:
        self._policies: dict[str, TenantPolicy] = {}

    async def get(self, tenant_id: str) -> TenantPolicy | None:
        return self._policies.get(tenant_id)

    async def put(self, tenant_id: str, policy: TenantPolicy) -> None:
        self._policies[tenant_id] = policy

    async def delete(self, tenant_id: str) -> None:
        self._policies.pop(tenant_id, None)


async def effective_policy(store: PolicyStore, tenant_id: str, default: TenantPolicy) -> TenantPolicy:
    policy = await store.get(tenant_id)
    return default if policy is None else policy
