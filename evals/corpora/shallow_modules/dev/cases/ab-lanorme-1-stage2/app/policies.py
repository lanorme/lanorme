"""Per-tenant guardrail policies and where they are kept.

``PolicyStore`` is the seam a database adapter would implement; the service
only ever talks to it through ``TenantPolicies``, which adds the fallback to
the default policy for tenants that have none stored.
"""

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DEFAULT_TENANT = "default"

Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TenantPolicy(BaseModel):
    """The guardrail settings one tenant's chat turns run under.

    Strict, so ``"yes"`` is not a boolean and ``5.0`` is not a tool-call limit:
    an admin typo is refused with 422 rather than silently reinterpreted.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # A tuple keeps a stored policy immutable; lax only so a JSON array, which
    # FastAPI hands over as a list, is accepted. Items stay strict strings.
    blocked_topics: tuple[Topic, ...] = Field(strict=False)
    redact_pii: bool
    max_tool_calls: int = Field(ge=0)
    system_prompt: str | None


class PolicyStore(Protocol):
    """Persistence for stored policies, keyed by tenant id."""

    async def get(self, tenant_id: str) -> TenantPolicy | None:
        """Return the tenant's stored policy, or ``None`` when it has none."""
        ...

    async def put(self, *, tenant_id: str, policy: TenantPolicy) -> None:
        """Store ``policy`` for the tenant, replacing any previous one."""
        ...

    async def delete(self, tenant_id: str) -> None:
        """Forget the tenant's policy; a tenant with none is not an error."""
        ...


class InMemoryPolicyStore:
    """A ``PolicyStore`` held in process memory, lost on restart."""

    def __init__(self) -> None:
        self._policies: dict[str, TenantPolicy] = {}

    async def get(self, tenant_id: str) -> TenantPolicy | None:
        """Return the tenant's stored policy, or ``None`` when it has none."""
        return self._policies.get(tenant_id)

    async def put(self, *, tenant_id: str, policy: TenantPolicy) -> None:
        """Store ``policy`` for the tenant, replacing any previous one."""
        self._policies[tenant_id] = policy

    async def delete(self, tenant_id: str) -> None:
        """Forget the tenant's policy; a tenant with none is not an error."""
        self._policies.pop(tenant_id, None)


class TenantPolicies:
    """Stored policies plus the default that applies wherever none is stored."""

    def __init__(self, *, store: PolicyStore, default: TenantPolicy) -> None:
        self._store = store
        self.default = default

    async def get_effective(self, tenant_id: str) -> TenantPolicy:
        """Return the policy the tenant's turns run under."""
        stored = await self._store.get(tenant_id)
        return self.default if stored is None else stored

    async def save(self, *, tenant_id: str, policy: TenantPolicy) -> TenantPolicy:
        """Store ``policy`` for the tenant and return it."""
        await self._store.put(tenant_id=tenant_id, policy=policy)
        return policy

    async def reset(self, tenant_id: str) -> None:
        """Drop the tenant's stored policy so it falls back to the default."""
        await self._store.delete(tenant_id)
