"""Port for storing the guardrail policies tenants have set."""

from typing import Protocol

from app.domain.policy import GuardrailPolicy


class PolicyRepository(Protocol):
    """Stored tenant policies; a tenant with none stored is simply absent."""

    async def get_policy(self, tenant_id: str) -> GuardrailPolicy | None:
        """Return the tenant's stored policy, or None when it has none."""
        ...

    async def save_policy(self, *, tenant_id: str, policy: GuardrailPolicy) -> None:
        """Store the policy, replacing any the tenant already had."""
        ...

    async def delete_policy(self, tenant_id: str) -> None:
        """Forget the tenant's policy; deleting an absent policy is not an error."""
        ...
