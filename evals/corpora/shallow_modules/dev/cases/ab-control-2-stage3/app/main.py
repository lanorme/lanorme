"""FastAPI service exposing a guarded deep agent."""

from __future__ import annotations

import hmac
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.agent import GuardedAgent, TurnResult, build_model
from app.config import Settings
from app.guardrails import TOPIC_BLOCKLIST
from app.policies import DEFAULT_TENANT, InMemoryPolicyStore, PolicyStore, TenantPolicy, effective_policy
from app.sessions import ConversationStore, InMemoryConversationStore, StoredMessage

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: str
    message: str


class GuardrailAction(BaseModel):
    name: str
    action: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    guardrails: list[GuardrailAction]


class SessionMessages(BaseModel):
    session_id: str
    messages: list[StoredMessage]


def tenant_id_from_header(x_tenant_id: Annotated[str | None, Header()] = None) -> str:
    """The calling tenant: the `X-Tenant-ID` header, or "default" when absent or blank."""
    if x_tenant_id is None or not x_tenant_id.strip():
        return DEFAULT_TENANT
    return x_tenant_id


def policy_store(request: Request) -> PolicyStore:
    return request.app.state.policy_store


Store = Annotated[PolicyStore, Depends(policy_store)]


def conversation_store(request: Request) -> ConversationStore:
    return request.app.state.conversation_store


Conversations = Annotated[ConversationStore, Depends(conversation_store)]
TenantId = Annotated[str, Depends(tenant_id_from_header)]

BLOCKED_RESPONSE = {"error": "blocked", "guardrail": TOPIC_BLOCKLIST}
AGENT_ERROR_RESPONSE = {"error": "agent_error"}

# A word with its trailing whitespace, or leading whitespace on its own. Every
# character matches, so the pieces always join back into the original text.
_TOKEN_RE = re.compile(r"\S+\s*|\s+")


def reply_tokens(reply: str) -> list[str]:
    return _TOKEN_RE.findall(reply)


def sse_event(payload: dict[str, Any]) -> str:
    # json.dumps escapes newlines, so each event is a single `data:` line.
    return f"data: {json.dumps(payload)}\n\n"


def admin_router(admin_api_key: str | None, default_policy: TenantPolicy) -> APIRouter:
    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        # Fails closed: with no ADMIN_API_KEY configured, nobody is an admin.
        if (
            not admin_api_key
            or x_admin_key is None
            or not hmac.compare_digest(x_admin_key.encode(), admin_api_key.encode())
        ):
            raise HTTPException(status_code=401, detail="invalid or missing admin key")

    router = APIRouter(prefix="/tenants/{tenant_id}/policy", dependencies=[Depends(require_admin)])

    @router.get("", response_model=TenantPolicy, responses={401: {"description": "Bad admin key"}})
    async def get_policy(tenant_id: str, store: Store) -> TenantPolicy:
        return await effective_policy(store, tenant_id, default_policy)

    # The body is parsed by hand, after `require_admin`, so a request without a
    # valid admin key always gets 401, even when its body is also invalid.
    @router.put(
        "",
        response_model=TenantPolicy,
        responses={401: {"description": "Bad admin key"}},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": TenantPolicy.model_json_schema()}},
            }
        },
    )
    async def put_policy(tenant_id: str, request: Request, store: Store) -> TenantPolicy:
        try:
            policy = TenantPolicy.model_validate_json(await request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False)) from None
        await store.put(tenant_id, policy)
        return policy

    @router.delete("", status_code=204, responses={401: {"description": "Bad admin key"}})
    async def delete_policy(tenant_id: str, store: Store) -> Response:
        await store.delete(tenant_id)
        return Response(status_code=204)

    return router


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    settings = Settings.from_env()
    if model is None:
        model = build_model(settings)
    agent = GuardedAgent(model, settings)

    app = FastAPI(title="Deep agent service")
    # Swap for a database-backed `PolicyStore` to persist policies.
    app.state.policy_store = InMemoryPolicyStore()
    # Swap for a database-backed `ConversationStore` to persist conversations.
    app.state.conversation_store = InMemoryConversationStore()
    app.include_router(admin_router(settings.admin_api_key, agent.default_policy))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def run_chat(
        request: ChatRequest, tenant_id: str, store: PolicyStore, conversations: ConversationStore
    ) -> TurnResult | JSONResponse:
        """Run one turn of the session's conversation and store it.

        Returns the turn, or the error response to send instead. Refused and
        failed turns are not stored.
        """
        try:
            policy = await effective_policy(store, tenant_id, agent.default_policy)
            history = await conversations.get(tenant_id, request.session_id) or []
            result = await agent.run_turn(request.message, policy, history)
            if not result.blocked:
                await conversations.append(
                    tenant_id,
                    request.session_id,
                    [
                        StoredMessage(role="user", content=result.user_message),
                        StoredMessage(role="assistant", content=result.reply),
                    ],
                )
        except Exception:
            logger.exception("agent turn failed for tenant %r session %s", tenant_id, request.session_id)
            return JSONResponse(status_code=502, content=AGENT_ERROR_RESPONSE)
        if result.blocked:
            return JSONResponse(status_code=403, content=BLOCKED_RESPONSE)
        return result

    chat_responses: dict[int | str, dict[str, Any]] = {
        403: {"description": "Blocked by a guardrail"},
        502: {"description": "Agent failure"},
    }

    @app.post("/chat", response_model=ChatResponse, responses=chat_responses)
    async def chat(
        request: ChatRequest, tenant_id: TenantId, store: Store, conversations: Conversations
    ) -> ChatResponse | JSONResponse:
        result = await run_chat(request, tenant_id, store, conversations)
        if isinstance(result, JSONResponse):
            return result
        return ChatResponse(
            session_id=request.session_id,
            reply=result.reply,
            guardrails=[GuardrailAction(**g) for g in result.guardrails],
        )

    @app.post(
        "/chat/stream",
        response_class=StreamingResponse,
        response_model=None,
        responses={200: {"content": {"text/event-stream": {}}}, **chat_responses},
    )
    async def chat_stream(
        request: ChatRequest, tenant_id: TenantId, store: Store, conversations: Conversations
    ) -> StreamingResponse | JSONResponse:
        # The turn runs to completion before the stream opens. A model may write
        # text before deciding to call a tool, and `/chat` drops that text, so a
        # chunk cannot be known to belong to the reply until the turn is over.
        # Finishing first also lets refusals and failures keep their 403/502
        # JSON, and means the reply is redacted as a whole, so PII split across
        # model chunks is still caught.
        result = await run_chat(request, tenant_id, store, conversations)
        if isinstance(result, JSONResponse):
            return result

        async def events() -> AsyncIterator[str]:
            for token in reply_tokens(result.reply):
                yield sse_event({"type": "token", "content": token})
            yield sse_event({"type": "done", "session_id": request.session_id, "guardrails": result.guardrails})

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
    async def get_session_messages(
        session_id: str, tenant_id: TenantId, conversations: Conversations
    ) -> SessionMessages:
        messages = await conversations.get(tenant_id, session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionMessages(session_id=session_id, messages=messages)

    @app.delete("/sessions/{session_id}", status_code=204, responses={404: {"description": "Unknown session"}})
    async def delete_session(session_id: str, tenant_id: TenantId, conversations: Conversations) -> Response:
        if not await conversations.delete(tenant_id, session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return Response(status_code=204)

    return app
