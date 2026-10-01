"""Chat and health routes.

``/chat`` carries no authentication of its own: this service is meant to sit
behind a gateway that authenticates callers and sets ``X-Tenant-ID``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.chat import ChatService, TopicBlockedError
from app.dependencies import get_caller_policy, get_chat_service
from app.policies import TenantPolicy
from app.schemas import BlockedResponse, ChatRequest, ChatResponse, GuardrailOut, HealthResponse

router = APIRouter()


@router.get("/health")
async def get_health() -> HealthResponse:
    """Report that the process is up."""
    return HealthResponse(status="ok")


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={403: {"model": BlockedResponse, "description": "Blocked topic"}},
)
async def post_chat(
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    policy: Annotated[TenantPolicy, Depends(get_caller_policy)],
) -> ChatResponse | JSONResponse:
    """Run one turn of the agent under the caller's tenant policy; a blocked topic is a 403."""
    try:
        turn = await service.reply(message=body.message, policy=policy)
    except TopicBlockedError as blocked:
        refusal = BlockedResponse(error=blocked.event.action, guardrail=blocked.event.name)
        return JSONResponse(status_code=403, content=refusal.model_dump())
    return ChatResponse(
        session_id=body.session_id,
        reply=turn.reply,
        guardrails=[GuardrailOut(name=e.name, action=e.action) for e in turn.guardrails],
    )
