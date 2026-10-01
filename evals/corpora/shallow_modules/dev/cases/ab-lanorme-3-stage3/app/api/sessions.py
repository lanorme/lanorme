"""Routes that read and forget a tenant's conversation, named by its session id."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.tenancy import build_conversation_key, resolve_tenant_id
from app.conversations import ConversationStore, UnknownConversationError
from app.schemas import ConversationResponse, MessageEntry

UNKNOWN_SESSION = "unknown session"


def build_session_router(*, conversations: ConversationStore) -> APIRouter:
    """Declare ``/sessions/{session_id}``, scoped to the tenant in ``X-Tenant-ID``."""
    router = APIRouter(prefix="/sessions/{session_id}")

    @router.get("/messages", responses={status.HTTP_404_NOT_FOUND: {"description": UNKNOWN_SESSION}})
    async def get_messages(
        *, session_id: str, tenant_id: Annotated[str, Depends(resolve_tenant_id)]
    ) -> ConversationResponse:
        key = build_conversation_key(tenant_id=tenant_id, session_id=session_id)
        messages = await conversations.list_messages(key)
        if messages is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=UNKNOWN_SESSION)
        return ConversationResponse(
            session_id=session_id, messages=[MessageEntry.from_message(message) for message in messages]
        )

    @router.delete(
        "",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={status.HTTP_404_NOT_FOUND: {"description": UNKNOWN_SESSION}},
    )
    async def delete_session(*, session_id: str, tenant_id: Annotated[str, Depends(resolve_tenant_id)]) -> Response:
        key = build_conversation_key(tenant_id=tenant_id, session_id=session_id)
        try:
            await conversations.delete_conversation(key)
        except UnknownConversationError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=UNKNOWN_SESSION) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
