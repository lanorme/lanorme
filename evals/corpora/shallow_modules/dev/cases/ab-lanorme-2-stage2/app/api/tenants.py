"""Admin endpoints for reading, replacing and resetting a tenant's guardrail policy."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from app.api.schemas import PolicyBody
from app.application.services.policies import PolicyService

POLICY_PATH = "/tenants/{tenant_id}/policy"

# Each endpoint names require_admin itself, so none can lose it by moving routers.
router = APIRouter(
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Missing or wrong X-Admin-Key"}}
)


def get_policy_service(request: Request) -> PolicyService:
    """Return the policy service that create_app stored on the application state."""
    return request.app.state.policy_service


def require_admin(
    request: Request, *, x_admin_key: Annotated[str | None, Header()] = None
) -> None:
    """Refuse with 401 unless X-Admin-Key equals the configured admin key.

    With no admin key configured every request is refused, so the endpoints
    fail closed rather than open.
    """
    expected: bytes | None = request.app.state.admin_api_key
    if not expected or x_admin_key is None or not hmac.compare_digest(
        x_admin_key.encode(), expected
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


@router.get(POLICY_PATH, dependencies=[Depends(require_admin)])
async def get_policy(
    tenant_id: str, service: Annotated[PolicyService, Depends(get_policy_service)]
) -> PolicyBody:
    """Return the tenant's effective policy: its own, else the default."""
    return PolicyBody.from_policy(await service.get_policy(tenant_id))


@router.put(POLICY_PATH, dependencies=[Depends(require_admin)])
async def put_policy(
    tenant_id: str,
    *,
    body: PolicyBody,
    service: Annotated[PolicyService, Depends(get_policy_service)],
) -> PolicyBody:
    """Store the tenant's policy, replacing any it had, and return it."""
    stored = await service.save_policy(tenant_id=tenant_id, policy=body.to_policy())
    return PolicyBody.from_policy(stored)


@router.delete(
    POLICY_PATH, status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)]
)
async def delete_policy(
    tenant_id: str, service: Annotated[PolicyService, Depends(get_policy_service)]
) -> Response:
    """Drop the tenant's policy so it falls back to the default."""
    await service.reset_policy(tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

