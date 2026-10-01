"""Which tenant a request acts for, taken from the ``X-Tenant-ID`` header."""

from typing import Annotated

from fastapi import Header

from app.conversations import ConversationKey
from app.policies import DEFAULT_TENANT


async def resolve_tenant_id(x_tenant_id: Annotated[str | None, Header()] = None) -> str:
    """Name the calling tenant; a missing or empty header means the default tenant.

    This identifies the tenant; it does not authenticate the caller.
    """
    return x_tenant_id or DEFAULT_TENANT


def build_conversation_key(*, tenant_id: str, session_id: str) -> ConversationKey:
    """Scope a session to its tenant, so equal session ids under two tenants stay apart."""
    return ConversationKey(tenant_id=tenant_id, session_id=session_id)
