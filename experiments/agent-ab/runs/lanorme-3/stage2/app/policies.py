"""Per-tenant guardrail policies and where they are kept.

A tenant without a stored policy gets the default one, which comes from the
service settings. Storage sits behind ``PolicyStore`` so the in-memory version
used today can be swapped for a database without touching the callers.
"""

from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

DEFAULT_TENANT = "default"


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    """How the guardrails treat one tenant's chat turns.

    ``system_prompt``, when set, is appended to the agent's own instructions.
    """

    blocked_topics: tuple[str, ...]
    redact_pii: bool
    max_tool_calls: int
    system_prompt: str | None


def build_default_policy(settings: Settings) -> GuardrailPolicy:
    """The policy for tenants that have none stored, from the service settings."""
    return GuardrailPolicy(
        blocked_topics=settings.blocked_topics,
        redact_pii=True,
        max_tool_calls=settings.max_tool_calls,
        system_prompt=None,
    )


class PolicyStore(Protocol):
    """Persistence for tenant policies; only explicitly stored ones live here."""

    async def get_policy(self, tenant_id: str) -> GuardrailPolicy | None:
        """Return the tenant's stored policy, or ``None`` when it has none."""
        ...

    async def save_policy(self, *, tenant_id: str, policy: GuardrailPolicy) -> None:
        """Store the tenant's policy, replacing any earlier one."""
        ...

    async def delete_policy(self, tenant_id: str) -> None:
        """Forget the tenant's policy; a tenant with none is left as it is."""
        ...


class InMemoryPolicyStore:
    """A ``PolicyStore`` held in a dict; policies are lost when the process exits."""

    def __init__(self) -> None:
        self._policies: dict[str, GuardrailPolicy] = {}

    async def get_policy(self, tenant_id: str) -> GuardrailPolicy | None:
        """Return the tenant's stored policy, or ``None`` when it has none."""
        return self._policies.get(tenant_id)

    async def save_policy(self, *, tenant_id: str, policy: GuardrailPolicy) -> None:
        """Store the tenant's policy, replacing any earlier one."""
        self._policies[tenant_id] = policy

    async def delete_policy(self, tenant_id: str) -> None:
        """Forget the tenant's policy; a tenant with none is left as it is."""
        self._policies.pop(tenant_id, None)


class TenantPolicies:
    """Resolves each tenant's effective policy: its stored one, else the default."""

    def __init__(self, *, store: PolicyStore, default: GuardrailPolicy) -> None:
        self._store = store
        self._default = default

    async def get_effective_policy(self, tenant_id: str) -> GuardrailPolicy:
        """Return the policy that governs the tenant's turns right now."""
        stored = await self._store.get_policy(tenant_id)
        return self._default if stored is None else stored

    async def save_policy(self, *, tenant_id: str, policy: GuardrailPolicy) -> None:
        """Give the tenant its own policy."""
        await self._store.save_policy(tenant_id=tenant_id, policy=policy)

    async def delete_policy(self, tenant_id: str) -> None:
        """Return the tenant to the default policy."""
        await self._store.delete_policy(tenant_id)
