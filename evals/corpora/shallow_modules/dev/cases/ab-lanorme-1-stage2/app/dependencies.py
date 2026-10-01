"""FastAPI dependencies: services stored by ``create_app``, the caller's tenant, admin auth."""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import SecretStr

from app.chat import ChatService
from app.policies import DEFAULT_TENANT, TenantPolicies, TenantPolicy


def get_chat_service(request: Request) -> ChatService:
    """Fetch the chat service that ``create_app`` stored on the application."""
    return request.app.state.chat_service


def get_tenant_policies(request: Request) -> TenantPolicies:
    """Fetch the tenant policies that ``create_app`` stored on the application."""
    return request.app.state.tenant_policies


async def get_caller_policy(
    policies: Annotated[TenantPolicies, Depends(get_tenant_policies)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TenantPolicy:
    """Return the effective policy of the tenant named by ``X-Tenant-ID``.

    The header is trusted as sent: the gateway in front of this service is
    expected to authenticate the caller and set it.
    """
    return await policies.get_effective(DEFAULT_TENANT if x_tenant_id is None else x_tenant_id)


def require_admin_key(
    *, request: Request, x_admin_key: Annotated[str | None, Header()] = None
) -> None:
    """Refuse with 401 unless ``X-Admin-Key`` equals the configured ``ADMIN_API_KEY``.

    With no key configured every request is refused, so a deployment that
    forgot to set one fails closed.
    """
    expected: SecretStr | None = request.app.state.admin_api_key
    if (
        expected is None
        or x_admin_key is None
        or not secrets.compare_digest(
            x_admin_key.encode(), expected.get_secret_value().encode()
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid admin key"
        )
