"""Resolution of the policy a tenant's turns run under."""

from app.application.ports.policy_repository import PolicyRepository
from app.domain.policy import GuardrailPolicy


class PolicyService:
    """Stores tenant policies and falls back to the default for tenants without one."""

    def __init__(self, *, repository: PolicyRepository, default: GuardrailPolicy) -> None:
        self._repository = repository
        self._default = default

    async def get_policy(self, tenant_id: str) -> GuardrailPolicy:
        """Return the tenant's effective policy: its stored one, else the default."""
        stored = await self._repository.get_policy(tenant_id)
        return stored if stored is not None else self._default

    async def save_policy(self, *, tenant_id: str, policy: GuardrailPolicy) -> GuardrailPolicy:
        """Store the tenant's policy and return it."""
        await self._repository.save_policy(tenant_id=tenant_id, policy=policy)
        return policy

    async def reset_policy(self, tenant_id: str) -> None:
        """Drop the tenant's stored policy so it falls back to the default."""
        await self._repository.delete_policy(tenant_id)
