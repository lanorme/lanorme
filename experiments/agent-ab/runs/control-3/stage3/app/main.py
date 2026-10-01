"""FastAPI entry point: `uv run uvicorn app.main:create_app --factory`."""

import hmac
import json
import logging
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from app.agent import build_agent
from app.config import Settings
from app.guardrails import (
    LIMIT_REACHED_REPLY,
    PII_REDACTION,
    TOOL_CALL_LIMIT,
    TOPIC_BLOCKLIST,
    TopicBlocklist,
    redact_pii,
    tenant_instructions,
    turn_budget,
)
from app.policies import DEFAULT_TENANT, InMemoryPolicyStore, Policy, PolicyStore, default_policy
from app.sessions import ChatMessage, InMemorySessionStore, SessionStore

logger = logging.getLogger(__name__)

MAX_TENANT_ID_LENGTH = 128

TenantId = Annotated[str, Path(min_length=1, max_length=MAX_TENANT_ID_LENGTH)]
TenantHeader = Annotated[str | None, Header(alias="X-Tenant-ID", max_length=MAX_TENANT_ID_LENGTH)]


class ChatRequest(BaseModel):
    session_id: str
    message: str


class GuardrailEvent(BaseModel):
    name: str
    action: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    guardrails: list[GuardrailEvent]


class SessionMessages(BaseModel):
    session_id: str
    messages: list[ChatMessage]


def _tenant(x_tenant_id: TenantHeader = None) -> str:
    return (x_tenant_id or "").strip() or DEFAULT_TENANT


Tenant = Annotated[str, Depends(_tenant)]


# Split points for streamed replies: after a run of whitespace, before the next word.
_TOKEN_BOUNDARY = re.compile(r"(?<=\s)(?=\S)")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _final_reply(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message.text
    return ""


@lru_cache(maxsize=256)
def _blocklist(topics: tuple[str, ...]) -> TopicBlocklist:
    return TopicBlocklist(topics)


def create_app(model: BaseChatModel | None = None, *, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if model is None:
        from langchain.chat_models import init_chat_model

        model = init_chat_model(settings.model)

    agent = build_agent(model, settings)
    fallback_policy = default_policy(settings)
    admin_key = settings.admin_api_key
    if not admin_key:
        logger.warning("ADMIN_API_KEY is not set; tenant policy endpoints will reject every request")

    app = FastAPI(title="Guarded deep agent")
    # Swap for database-backed stores here; handlers read them per request.
    app.state.policy_store = InMemoryPolicyStore()
    app.state.session_store = InMemorySessionStore()

    def store(request: Request) -> PolicyStore:
        return request.app.state.policy_store

    def sessions(request: Request) -> SessionStore:
        return request.app.state.session_store

    async def effective_policy(request: Request, tenant_id: str) -> Policy:
        return await store(request).get(tenant_id) or fallback_policy

    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        if not (
            admin_key
            and x_admin_key is not None
            and hmac.compare_digest(x_admin_key.encode(), admin_key.encode())
        ):
            raise HTTPException(status_code=401, detail="invalid or missing admin key")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def run_turn(
        request: Request, tenant_id: str, body: ChatRequest
    ) -> ChatResponse | JSONResponse:
        """One chat turn, shared by /chat and /chat/stream: guardrails, memory, agent."""
        policy = await effective_policy(request, tenant_id)
        events: list[GuardrailEvent] = []

        if _blocklist(tuple(policy.blocked_topics)).find(body.message):
            return JSONResponse(
                status_code=403,
                content={"error": "blocked", "guardrail": TOPIC_BLOCKLIST},
            )

        message, redacted_in = (
            redact_pii(body.message) if policy.redact_pii else (body.message, False)
        )

        # Earlier turns are stored as they were redacted at the time. Re-apply the
        # current policy so a tenant that has since turned redaction on never
        # sends older unredacted text to the model (redaction is idempotent).
        history = await sessions(request).get(tenant_id, body.session_id) or []
        if policy.redact_pii:
            history = [m.model_copy(update={"content": redact_pii(m.content)[0]}) for m in history]
        messages = [m.model_dump() for m in history] + [{"role": "user", "content": message}]

        with (
            turn_budget(policy.max_tool_calls) as budget,
            tenant_instructions(policy.system_prompt),
        ):
            try:
                result = await agent.ainvoke({"messages": messages})
            except Exception:
                logger.exception(
                    "agent failed for tenant %s session %s", tenant_id, body.session_id
                )
                return JSONResponse(status_code=502, content={"error": "agent_error"})

        if budget.exceeded:
            reply = LIMIT_REACHED_REPLY.format(limit=policy.max_tool_calls)
            events.append(GuardrailEvent(name=TOOL_CALL_LIMIT, action="stopped"))
        else:
            reply = _final_reply(result["messages"])

        reply, redacted_out = redact_pii(reply) if policy.redact_pii else (reply, False)
        if redacted_in or redacted_out:
            events.insert(0, GuardrailEvent(name=PII_REDACTION, action="redacted"))

        await sessions(request).append(
            tenant_id,
            body.session_id,
            [ChatMessage(role="user", content=message), ChatMessage(role="assistant", content=reply)],
        )
        return ChatResponse(session_id=body.session_id, reply=reply, guardrails=events)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: Request, body: ChatRequest, tenant_id: Tenant):
        return await run_turn(request, tenant_id, body)

    @app.post("/chat/stream", response_model=None)
    async def chat_stream(request: Request, body: ChatRequest, tenant_id: Tenant):
        # The reply /chat returns is the agent's last message, and text a model
        # writes alongside a tool call is not part of it; which message is last is
        # only known once the turn ends. So the turn runs to completion first,
        # then its (already redacted) reply is streamed: the tokens always add up
        # to the /chat reply and redaction never depends on chunk boundaries.
        # Failures before that (403, 502) are plain JSON, as on /chat.
        turn = await run_turn(request, tenant_id, body)
        if isinstance(turn, JSONResponse):
            return turn

        async def events() -> AsyncIterator[str]:
            for piece in _TOKEN_BOUNDARY.split(turn.reply):
                if piece:
                    yield _sse({"type": "token", "content": piece})
            yield _sse(
                {
                    "type": "done",
                    "session_id": turn.session_id,
                    "guardrails": [g.model_dump() for g in turn.guardrails],
                }
            )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sessions/{session_id}/messages", response_model=SessionMessages)
    async def get_session_messages(
        request: Request, session_id: str, tenant_id: Tenant
    ) -> SessionMessages:
        messages = await sessions(request).get(tenant_id, session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionMessages(session_id=session_id, messages=messages)

    @app.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(request: Request, session_id: str, tenant_id: Tenant) -> Response:
        if not await sessions(request).delete(tenant_id, session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return Response(status_code=204)

    tenants = APIRouter(prefix="/tenants/{tenant_id}", dependencies=[Depends(require_admin)])

    @tenants.put("/policy", response_model=Policy)
    async def put_policy(request: Request, tenant_id: TenantId, policy: Policy) -> Policy:
        return await store(request).put(tenant_id, policy)

    @tenants.get("/policy", response_model=Policy)
    async def get_policy(request: Request, tenant_id: TenantId) -> Policy:
        return await effective_policy(request, tenant_id)

    @tenants.delete("/policy", status_code=204)
    async def delete_policy(request: Request, tenant_id: TenantId) -> Response:
        await store(request).delete(tenant_id)
        return Response(status_code=204)

    app.include_router(tenants)
    return app
