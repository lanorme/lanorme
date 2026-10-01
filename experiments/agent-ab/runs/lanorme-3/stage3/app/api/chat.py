"""The public routes: the health probe and the guarded chat turn, whole or streamed."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.agent import ChatTurn, GuardedChat, ReplyChunk, TopicBlockedError
from app.api.tenancy import build_conversation_key, resolve_tenant_id
from app.guardrails import TOPIC_BLOCKLIST
from app.policies import TenantPolicies
from app.schemas import BlockedResponse, ChatRequest, ChatResponse, DoneEvent, GuardrailEntry, HealthResponse, TokenEvent

BLOCKED_RESPONSES: dict[int | str, dict[str, object]] = {status.HTTP_403_FORBIDDEN: {"model": BlockedResponse}}
# Proxies such as nginx otherwise buffer the stream and deliver it in one go.
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def build_chat_router(*, chat: GuardedChat, policies: TenantPolicies) -> APIRouter:
    """Declare ``/health``, ``/chat`` and ``/chat/stream`` over one shared ``GuardedChat``."""
    router = APIRouter()

    @router.get("/health")
    async def get_health() -> HealthResponse:
        return HealthResponse()

    @router.post("/chat", response_model=ChatResponse, responses=BLOCKED_RESPONSES)
    async def post_chat(
        *, request: ChatRequest, tenant_id: Annotated[str, Depends(resolve_tenant_id)]
    ) -> ChatResponse | JSONResponse:
        policy = await policies.get_effective_policy(tenant_id)
        conversation = build_conversation_key(tenant_id=tenant_id, session_id=request.session_id)
        try:
            turn = await chat.run_turn(request.message, conversation=conversation, policy=policy)
        except TopicBlockedError:
            return build_blocked_response()
        return ChatResponse(
            session_id=request.session_id,
            reply=turn.reply,
            guardrails=[GuardrailEntry.from_action(entry) for entry in turn.guardrails],
        )

    @router.post(
        "/chat/stream",
        response_class=StreamingResponse,
        response_model=None,
        responses={status.HTTP_200_OK: {"content": {"text/event-stream": {}}}} | BLOCKED_RESPONSES,
    )
    async def post_chat_stream(
        *, request: ChatRequest, tenant_id: Annotated[str, Depends(resolve_tenant_id)]
    ) -> StreamingResponse | JSONResponse:
        policy = await policies.get_effective_policy(tenant_id)
        conversation = build_conversation_key(tenant_id=tenant_id, session_id=request.session_id)
        try:
            events = chat.stream_turn(request.message, conversation=conversation, policy=policy)
        except TopicBlockedError:
            return build_blocked_response()
        return StreamingResponse(
            encode_events(events=events, session_id=request.session_id),
            media_type="text/event-stream",
            headers=STREAM_HEADERS,
        )

    return router


def build_blocked_response() -> JSONResponse:
    """The 403 for a message the topic blocklist refuses."""
    blocked = BlockedResponse(guardrail=TOPIC_BLOCKLIST)
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=blocked.model_dump())


async def encode_events(*, events: AsyncIterator[ReplyChunk | ChatTurn], session_id: str) -> AsyncIterator[str]:
    """Frame each turn event as a server-sent event: a token per chunk, then ``done``."""
    async for event in events:
        match event:
            case ReplyChunk(text=text):
                yield format_sse(TokenEvent(content=text))
            case ChatTurn(guardrails=guardrails):
                done = DoneEvent(
                    session_id=session_id, guardrails=[GuardrailEntry.from_action(entry) for entry in guardrails]
                )
                yield format_sse(done)


def format_sse(event: BaseModel) -> str:
    """One ``data:`` line holding the event as JSON, then the blank line that ends it."""
    return f"data: {event.model_dump_json()}\n\n"
