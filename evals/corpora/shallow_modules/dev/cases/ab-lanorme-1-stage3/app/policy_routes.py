"""Admin routes for reading and replacing a tenant's guardrail policy."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.dependencies import get_tenant_policies, require_admin_key
from app.policies import TenantPolicies, TenantPolicy

router = APIRouter(
    prefix="/tenants/{tenant_id}/policy",
    tags=["tenant policies"],
    dependencies=[Depends(require_admin_key)],
    responses={401: {"description": "Missing or invalid X-Admin-Key"}},
)

Policies = Annotated[TenantPolicies, Depends(get_tenant_policies)]


@router.get("")
async def get_policy(*, tenant_id: str, policies: Policies) -> TenantPolicy:
    """Return the tenant's effective policy: its stored one, else the default."""
    return await policies.get_effective(tenant_id)


@router.put("")
async def put_policy(*, tenant_id: str, policy: TenantPolicy, policies: Policies) -> TenantPolicy:
    """Replace the tenant's policy with the full policy in the body."""
    return await policies.save(tenant_id=tenant_id, policy=policy)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(*, tenant_id: str, policies: Policies) -> Response:
    """Drop the tenant's stored policy; it falls back to the default."""
    await policies.reset(tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
