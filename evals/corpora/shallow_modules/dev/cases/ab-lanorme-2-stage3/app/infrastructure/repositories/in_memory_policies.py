"""PolicyRepository kept in process memory; policies are lost on restart."""

from app.application.ports.policy_repository import PolicyRepository
from app.domain.policy import GuardrailPolicy


class InMemoryPolicyRepository(PolicyRepository):
    """A dict of tenant ID to policy. Policies are immutable, so they are stored as given."""

    def __init__(self) -> None:
        self._policies: dict[str, GuardrailPolicy] = {}

    async def get_policy(self, tenant_id: str) -> GuardrailPolicy | None:
        """Return the tenant's stored policy, or None."""
        return self._policies.get(tenant_id)

    async def save_policy(self, *, tenant_id: str, policy: GuardrailPolicy) -> None:
        """Store or replace the tenant's policy."""
        self._policies[tenant_id] = policy

    async def delete_policy(self, tenant_id: str) -> None:
        """Forget the tenant's policy if it has one."""
        self._policies.pop(tenant_id, None)
