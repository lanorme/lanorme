"""Chat and health routes.

``/chat`` and ``/chat/stream`` carry no authentication of their own: this
service is meant to sit behind a gateway that authenticates callers and sets
``X-Tenant-ID``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.chat import ChatService, ChatTurn, ReplyStream, TopicBlockedError
from app.conversations import SessionKey
from app.dependencies import get_caller_policy, get_caller_tenant, get_chat_service
from app.policies import TenantPolicy
from app.schemas import (
    BlockedResponse,
    ChatRequest,
    ChatResponse,
    DoneEvent,
    GuardrailOut,
    HealthResponse,
    TokenEvent,
)

router = APIRouter()

Service = Annotated[ChatService, Depends(get_chat_service)]
CallerPolicy = Annotated[TenantPolicy, Depends(get_caller_policy)]
CallerTenant = Annotated[str, Depends(get_caller_tenant)]
BLOCKED_RESPONSE = {403: {"model": BlockedResponse, "description": "Blocked topic"}}


@router.get("/health")
async def get_health() -> HealthResponse:
    """Report that the process is up."""
    return HealthResponse(status="ok")


@router.post("/chat", response_model=ChatResponse, responses=BLOCKED_RESPONSE)
async def post_chat(
    *, body: ChatRequest, service: Service, policy: CallerPolicy, tenant_id: CallerTenant
) -> ChatResponse | JSONResponse:
    """Run one turn of the session under the caller's tenant policy; a blocked topic is a 403."""
    key = SessionKey(tenant_id=tenant_id, session_id=body.session_id)
    try:
        turn = await service.reply(key=key, message=body.message, policy=policy)
    except TopicBlockedError as blocked:
        return _refusal(blocked)
    return ChatResponse(
        session_id=body.session_id, reply=turn.reply, guardrails=_guardrails_out(turn)
    )


@router.post(
    "/chat/stream",
    response_model=None,
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, **BLOCKED_RESPONSE},
)
async def post_chat_stream(
    *, body: ChatRequest, service: Service, policy: CallerPolicy, tenant_id: CallerTenant
) -> StreamingResponse | JSONResponse:
    """Run one turn like ``/chat``, sending the reply as server-sent events.

    ``token`` events carry the reply chunk by chunk; a final ``done`` event
    lists the guardrails. A blocked topic is the same 403 as ``/chat``.
    """
    key = SessionKey(tenant_id=tenant_id, session_id=body.session_id)
    try:
        stream = await service.start(key=key, message=body.message, policy=policy)
    except TopicBlockedError as blocked:
        return _refusal(blocked)
    return StreamingResponse(
        _server_sent_events(stream=stream, session_id=body.session_id),
        media_type="text/event-stream",
    )


async def _server_sent_events(*, stream: ReplyStream, session_id: str) -> AsyncIterator[str]:
    """Frame the reply's chunks, then the closing summary, as ``data:`` events."""
    async for chunk in stream.chunks:
        yield _event(TokenEvent(content=chunk))
    yield _event(DoneEvent(session_id=session_id, guardrails=_guardrails_out(stream.turn)))


def _event(payload: BaseModel) -> str:
    """Encode one server-sent event; JSON never contains a raw newline."""
    return f"data: {payload.model_dump_json()}\n\n"


def _guardrails_out(turn: ChatTurn) -> list[GuardrailOut]:
    """List the guardrails that acted on ``turn`` in their response shape."""
    return [GuardrailOut(name=event.name, action=event.action) for event in turn.guardrails]


def _refusal(blocked: TopicBlockedError) -> JSONResponse:
    """Build the 403 both chat endpoints return for a blocked topic."""
    refusal = BlockedResponse(error=blocked.event.action, guardrail=blocked.event.name)
    return JSONResponse(status_code=403, content=refusal.model_dump())
