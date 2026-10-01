"""Routes for reading and forgetting a conversation of the caller's tenant.

Like ``/chat``, these trust ``X-Tenant-ID`` as set by the gateway; a session
is only ever looked up within that tenant.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.conversations import ConversationStore, SessionKey, UnknownSessionError
from app.dependencies import get_caller_tenant, get_conversations
from app.schemas import MessageOut, SessionMessagesResponse

router = APIRouter(
    prefix="/sessions/{session_id}",
    tags=["sessions"],
    responses={404: {"description": "No such session for this tenant"}},
)

Conversations = Annotated[ConversationStore, Depends(get_conversations)]
CallerTenant = Annotated[str, Depends(get_caller_tenant)]


@router.get("/messages")
async def get_session_messages(
    *, session_id: str, tenant_id: CallerTenant, conversations: Conversations
) -> SessionMessagesResponse:
    """Return the session's messages in order, as stored (redacted where the policy redacts)."""
    messages = await conversations.load(SessionKey(tenant_id=tenant_id, session_id=session_id))
    if messages is None:
        raise _unknown_session()
    return SessionMessagesResponse(
        session_id=session_id,
        messages=[MessageOut(role=stored.role, content=stored.content) for stored in messages],
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    *, session_id: str, tenant_id: CallerTenant, conversations: Conversations
) -> Response:
    """Forget the session, so its next turn starts a new conversation."""
    try:
        await conversations.delete(SessionKey(tenant_id=tenant_id, session_id=session_id))
    except UnknownSessionError as unknown:
        raise _unknown_session() from unknown
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _unknown_session() -> HTTPException:
    """Build the 404 for a session the caller's tenant does not have."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown session")
