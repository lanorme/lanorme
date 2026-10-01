"""The public routes: the health probe and the guarded chat turn."""

from typing import Annotated

from fastapi import APIRouter, Header, status
from fastapi.responses import JSONResponse

from app.agent import GuardedChat, TopicBlockedError
from app.guardrails import TOPIC_BLOCKLIST
from app.policies import DEFAULT_TENANT, TenantPolicies
from app.schemas import BlockedResponse, ChatRequest, ChatResponse, GuardrailEntry, HealthResponse


def build_chat_router(*, chat: GuardedChat, policies: TenantPolicies) -> APIRouter:
    """Declare ``/health`` and ``/chat`` over one shared ``GuardedChat``."""
    router = APIRouter()

    @router.get("/health")
    async def get_health() -> HealthResponse:
        return HealthResponse()

    @router.post(
        "/chat",
        response_model=ChatResponse,
        responses={status.HTTP_403_FORBIDDEN: {"model": BlockedResponse}},
    )
    async def post_chat(
        *, request: ChatRequest, x_tenant_id: Annotated[str | None, Header()] = None
    ) -> ChatResponse | JSONResponse:
        policy = await policies.get_effective_policy(x_tenant_id or DEFAULT_TENANT)
        try:
            turn = await chat.run_turn(request.message, policy=policy)
        except TopicBlockedError:
            blocked = BlockedResponse(guardrail=TOPIC_BLOCKLIST)
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=blocked.model_dump())
        return ChatResponse(
            session_id=request.session_id,
            reply=turn.reply,
            guardrails=[GuardrailEntry(name=entry.name, action=entry.action) for entry in turn.guardrails],
        )

    return router
