"""Endpoints for reading and forgetting a tenant's conversations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.routes import get_tenant_id
from app.api.schemas import ErrorResponse, SessionMessagesResponse
from app.application.services.conversations import ConversationService
from app.domain.conversation import SessionKey
from app.domain.errors import SessionNotFoundError

SESSION_NOT_FOUND = "session_not_found"

router = APIRouter(
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Unknown session"}}
)


def get_conversation_service(request: Request) -> ConversationService:
    """Return the conversation service that create_app stored on the application state."""
    return request.app.state.conversation_service


def get_session_key(session_id: str, tenant_id: Annotated[str, Depends(get_tenant_id)]) -> SessionKey:
    """Return the session named in the path, scoped to the tenant named by X-Tenant-ID."""
    return SessionKey(tenant_id=tenant_id, session_id=session_id)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(
    key: Annotated[SessionKey, Depends(get_session_key)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> SessionMessagesResponse | JSONResponse:
    """Return the session's messages in order, as stored; 404 when unknown."""
    try:
        messages = await service.list_messages(key)
    except SessionNotFoundError:
        return _build_not_found_response()
    return SessionMessagesResponse.from_messages(session_id=key.session_id, messages=messages)


# Unauthenticated like /chat, whose sessions it forgets: callers authenticate upstream.
@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(  # lanorme: ignore[AUTHN-001]
    key: Annotated[SessionKey, Depends(get_session_key)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> Response:
    """Forget the session's conversation; 404 when unknown."""
    try:
        await service.forget_conversation(key)
    except SessionNotFoundError:
        return _build_not_found_response()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _build_not_found_response() -> JSONResponse:
    error = ErrorResponse(error=SESSION_NOT_FOUND)
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content=error.model_dump(exclude_none=True)
    )
