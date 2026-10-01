"""HTTP endpoints: health check and guarded chat, whole or streamed."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    DoneEvent,
    ErrorResponse,
    GuardrailOut,
    HealthResponse,
    StreamErrorEvent,
    TokenEvent,
)
from app.application.services.chat import ChatService, ChatTurn, TurnEvent
from app.domain.conversation import SessionKey
from app.domain.errors import AgentUnavailableError, TopicBlockedError
from app.domain.guardrails import TOPIC_BLOCKED
from app.domain.policy import DEFAULT_TENANT

AGENT_UNAVAILABLE = "agent_unavailable"

router = APIRouter()

_REFUSALS = {
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
}
# Proxies such as nginx buffer responses unless told not to, which would hold tokens back.
_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def get_chat_service(request: Request) -> ChatService:
    """Return the chat service that create_app stored on the application state."""
    return request.app.state.chat_service


def get_tenant_id(x_tenant_id: Annotated[str | None, Header()] = None) -> str:
    """Return the tenant named by X-Tenant-ID; a missing or empty header means the default."""
    return x_tenant_id or DEFAULT_TENANT


@router.get("/health")
async def get_health() -> HealthResponse:
    """Report that the service is up."""
    return HealthResponse(status="ok")


# The contract fixes /chat as unauthenticated; callers authenticate upstream.
@router.post("/chat", response_model=ChatResponse, responses=_REFUSALS)
async def post_chat(  # lanorme: ignore[AUTHN-001]
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> ChatResponse | JSONResponse:
    """Answer one message under the tenant's guardrails; 403 when a topic is blocked."""
    key = SessionKey(tenant_id=tenant_id, session_id=body.session_id)
    try:
        turn = await service.respond(body.message, key=key)
    except TopicBlockedError:
        return _build_blocked_response()
    except AgentUnavailableError:
        return _build_unavailable_response()
    return ChatResponse(
        session_id=body.session_id,
        reply=turn.reply,
        guardrails=GuardrailOut.from_actions(turn.guardrails),
    )


# Same contract as /chat: unauthenticated, callers authenticate upstream.
@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    response_model=None,
    responses={
        status.HTTP_200_OK: {"content": {"text/event-stream": {}}},
        **_REFUSALS,
    },
)
async def post_chat_stream(  # lanorme: ignore[AUTHN-001]
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> StreamingResponse | JSONResponse:
    """Stream the reply as server-sent events: tokens, then done; 403 before any stream.

    The first event is awaited before the response starts, so a model that
    fails straight away still gets the 502 that /chat returns.
    """
    key = SessionKey(tenant_id=tenant_id, session_id=body.session_id)
    try:
        events = await service.start_turn(body.message, key=key)
        first = await anext(events)
    except TopicBlockedError:
        return _build_blocked_response()
    except AgentUnavailableError:
        return _build_unavailable_response()
    return StreamingResponse(
        _encode_stream(first=first, rest=events, session_id=body.session_id),
        media_type="text/event-stream",
        headers=_STREAM_HEADERS,
    )


async def _encode_stream(
    *, first: TurnEvent, rest: AsyncIterator[TurnEvent], session_id: str
) -> AsyncIterator[str]:
    yield _encode_event(first, session_id=session_id)
    try:
        async for event in rest:
            yield _encode_event(event, session_id=session_id)
    except AgentUnavailableError:
        yield _format_sse(StreamErrorEvent(error=AGENT_UNAVAILABLE))


def _encode_event(event: TurnEvent, *, session_id: str) -> str:
    if isinstance(event, ChatTurn):
        guardrails = GuardrailOut.from_actions(event.guardrails)
        return _format_sse(DoneEvent(session_id=session_id, guardrails=guardrails))
    return _format_sse(TokenEvent(content=event.text))


def _format_sse(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


def _build_blocked_response() -> JSONResponse:
    error = ErrorResponse(error=TOPIC_BLOCKED.action, guardrail=TOPIC_BLOCKED.name)
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=error.model_dump())


def _build_unavailable_response() -> JSONResponse:
    error = ErrorResponse(error=AGENT_UNAVAILABLE)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY, content=error.model_dump(exclude_none=True)
    )
