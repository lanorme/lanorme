"""FastAPI entry point: ``uv run uvicorn app.main:create_app --factory``."""

import hmac
import json
import os
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.agent import BlockedTopicError, GuardedAgent, TurnResult, build_model
from app.config import get_settings
from app.conversations import InMemoryConversationStore, StoredMessage
from app.guardrails import BLOCKLIST_EVENT, GuardrailEvent
from app.policies import (
    DEFAULT_TENANT,
    InMemoryPolicyStore,
    PolicyService,
    TenantPolicy,
    default_policy,
)


async def _read_policy(request: Request) -> TenantPolicy:
    """Parse a PUT body into a policy, as FastAPI would, but only once called.

    Declaring the body as a parameter would make FastAPI reject malformed JSON
    with 422 before the admin-key check runs; unauthenticated callers get 401.
    """
    try:
        raw = await request.json()
    except ValueError as exc:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": "JSON decode error", "input": {},
              "ctx": {"error": str(exc)}}]
        ) from exc
    try:
        return TenantPolicy.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors(include_url=False)]
        ) from exc


class ChatRequest(BaseModel):
    session_id: str
    message: str


class GuardrailOut(BaseModel):
    name: str
    action: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    guardrails: list[GuardrailOut]


class SessionMessages(BaseModel):
    session_id: str
    messages: list[StoredMessage]


def _guardrails_out(events: list[GuardrailEvent]) -> list[GuardrailOut]:
    return [GuardrailOut(name=e.name, action=e.action) for e in events]


def _blocked_response() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"error": "blocked", "guardrail": BLOCKLIST_EVENT.name},
    )


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    settings = get_settings()
    agent = GuardedAgent(model if model is not None else build_model(settings), settings)
    policies = PolicyService(InMemoryPolicyStore(), default_policy(settings))
    # Unset or empty means no key can match, so the admin endpoints stay closed.
    admin_key = os.environ.get("ADMIN_API_KEY", "")

    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        if not admin_key or x_admin_key is None or not hmac.compare_digest(
            x_admin_key.encode(), admin_key.encode()
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")

    app = FastAPI(title="Guarded deep agent")
    app.state.agent = agent
    # Swap ``app.state.policies.store`` for another PolicyStore (e.g. a database).
    app.state.policies = policies
    # Likewise ``app.state.conversations`` for another ConversationStore.
    app.state.conversations = InMemoryConversationStore()

    def tenant_of(x_tenant_id: Annotated[str | None, Header()] = None) -> str:
        return x_tenant_id or DEFAULT_TENANT

    Tenant = Annotated[str, Depends(tenant_of)]

    async def history(tenant_id: str, session_id: str) -> list[StoredMessage]:
        return await app.state.conversations.get(tenant_id, session_id) or []

    async def remember(tenant_id: str, session_id: str, result: TurnResult) -> None:
        await app.state.conversations.append(tenant_id, session_id, result.to_store())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={403: {"description": "Message blocked by a guardrail"}},
    )
    async def chat(request: ChatRequest, tenant_id: Tenant) -> ChatResponse | JSONResponse:
        policy = await policies.effective(tenant_id)
        past = await history(tenant_id, request.session_id)
        try:
            result = await agent.run_turn(request.message, policy, past)
        except BlockedTopicError:
            return _blocked_response()
        await remember(tenant_id, request.session_id, result)
        return ChatResponse(
            session_id=request.session_id,
            reply=result.reply,
            guardrails=_guardrails_out(result.guardrails),
        )

    @app.post(
        "/chat/stream",
        response_class=StreamingResponse,
        responses={
            200: {"content": {"text/event-stream": {}}, "description": "Server-sent events"},
            403: {"description": "Message blocked by a guardrail"},
        },
    )
    async def chat_stream(request: ChatRequest, tenant_id: Tenant) -> Response:
        policy = await policies.effective(tenant_id)
        past = await history(tenant_id, request.session_id)
        try:
            turn = agent.stream_turn(request.message, policy, past)
        except BlockedTopicError:
            return _blocked_response()

        async def events() -> AsyncIterator[str]:
            async for item in turn:
                if isinstance(item, str):
                    yield _sse({"type": "token", "content": item})
                else:
                    # Stored once the whole reply has been streamed, before "done".
                    await remember(tenant_id, request.session_id, item)
                    yield _sse(
                        {
                            "type": "done",
                            "session_id": request.session_id,
                            "guardrails": [g.model_dump() for g in _guardrails_out(item.guardrails)],
                        }
                    )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(
        "/sessions/{session_id}/messages",
        response_model=SessionMessages,
        responses={404: {"description": "Unknown session"}},
    )
    async def get_session_messages(session_id: str, tenant_id: Tenant) -> SessionMessages:
        messages = await app.state.conversations.get(tenant_id, session_id)
        if messages is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown session")
        return SessionMessages(session_id=session_id, messages=messages)

    @app.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"description": "Unknown session"}},
    )
    async def delete_session(session_id: str, tenant_id: Tenant) -> Response:
        if not await app.state.conversations.delete(tenant_id, session_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown session")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    admin = [Depends(require_admin)]
    unauthorized = {401: {"description": "Missing or wrong X-Admin-Key"}}

    @app.get(
        "/tenants/{tenant_id}/policy",
        response_model=TenantPolicy,
        dependencies=admin,
        responses=unauthorized,
    )
    async def get_policy(tenant_id: str) -> TenantPolicy:
        return await policies.effective(tenant_id)

    @app.put(
        "/tenants/{tenant_id}/policy",
        response_model=TenantPolicy,
        dependencies=admin,
        responses=unauthorized,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": TenantPolicy.model_json_schema()}},
            }
        },
    )
    async def put_policy(tenant_id: str, request: Request) -> TenantPolicy:
        policy = await _read_policy(request)
        await policies.store.put(tenant_id, policy)
        return policy

    @app.delete(
        "/tenants/{tenant_id}/policy",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=admin,
        responses=unauthorized,
    )
    async def delete_policy(tenant_id: str) -> Response:
        await policies.store.delete(tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
