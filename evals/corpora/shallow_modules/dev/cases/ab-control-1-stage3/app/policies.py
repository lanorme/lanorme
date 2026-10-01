"""Per-tenant guardrail policies and where they are stored."""

import threading
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from app.config import Settings

DEFAULT_TENANT = "default"


class TenantPolicy(BaseModel):
    """The guardrails applied to one tenant's chat turns.

    Strict types and ``extra="forbid"`` so that a typo or a stringly-typed value
    (``"redact_pii": "no"``) is rejected instead of silently weakening a guardrail.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocked_topics: list[StrictStr]
    redact_pii: StrictBool
    max_tool_calls: Annotated[StrictInt, Field(ge=0)]
    system_prompt: StrictStr | None

    @field_validator("blocked_topics")
    @classmethod
    def _clean_topics(cls, topics: list[str]) -> list[str]:
        # Blank entries never match anything; drop them so the stored policy shows
        # exactly what is enforced.
        return [t.strip() for t in topics if t.strip()]


def default_policy(settings: Settings) -> TenantPolicy:
    """The policy for tenants without a stored one.

    Blocked topics and the tool-call limit come from the ``AGENT_*`` settings, whose
    defaults are ``["weapons", "malware"]`` and 5.
    """
    return TenantPolicy(
        blocked_topics=settings.blocked_topics,
        redact_pii=True,
        max_tool_calls=settings.max_tool_calls,
        system_prompt=None,
    )


class PolicyStore(Protocol):
    """Storage for tenants' explicitly set policies; a database can implement this."""

    async def get(self, tenant_id: str) -> TenantPolicy | None: ...

    async def put(self, tenant_id: str, policy: TenantPolicy) -> None: ...

    async def delete(self, tenant_id: str) -> None:
        """Remove the tenant's policy; a no-op when none is stored."""
        ...


class InMemoryPolicyStore:
    """Process-local store; policies are lost on restart."""

    def __init__(self) -> None:
        self._policies: dict[str, TenantPolicy] = {}
        # A thread lock (never held across an await) works from any event loop.
        self._lock = threading.Lock()

    async def get(self, tenant_id: str) -> TenantPolicy | None:
        with self._lock:
            return self._policies.get(tenant_id)

    async def put(self, tenant_id: str, policy: TenantPolicy) -> None:
        with self._lock:
            self._policies[tenant_id] = policy

    async def delete(self, tenant_id: str) -> None:
        with self._lock:
            self._policies.pop(tenant_id, None)


class PolicyService:
    """Resolves a tenant's effective policy: the stored one, else the default."""

    def __init__(self, store: PolicyStore, default: TenantPolicy) -> None:
        self.store = store
        self.default = default

    async def effective(self, tenant_id: str) -> TenantPolicy:
        stored = await self.store.get(tenant_id)
        return stored if stored is not None else self.default
