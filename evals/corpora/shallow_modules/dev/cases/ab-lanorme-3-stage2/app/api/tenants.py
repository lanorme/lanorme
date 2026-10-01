"""Admin routes that read, replace and reset a tenant's guardrail policy."""

from fastapi import APIRouter, Depends, Response, status

from app.api.admin_auth import AdminKeyCheck
from app.policies import TenantPolicies
from app.schemas import PolicyBody


def build_tenant_router(*, policies: TenantPolicies, require_admin_key: AdminKeyCheck) -> APIRouter:
    """Declare ``/tenants/{tenant_id}/policy``; every route needs the admin key."""
    router = APIRouter(prefix="/tenants/{tenant_id}/policy")

    @router.get("", dependencies=[Depends(require_admin_key)])
    async def get_policy(tenant_id: str) -> PolicyBody:
        return PolicyBody.from_policy(await policies.get_effective_policy(tenant_id))

    @router.put("", dependencies=[Depends(require_admin_key)])
    async def put_policy(*, tenant_id: str, body: PolicyBody) -> PolicyBody:
        await policies.save_policy(tenant_id=tenant_id, policy=body.to_policy())
        return body

    @router.delete("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin_key)])
    async def delete_policy(tenant_id: str) -> Response:
        await policies.delete_policy(tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
