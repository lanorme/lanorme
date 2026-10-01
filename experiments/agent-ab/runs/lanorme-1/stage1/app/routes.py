"""HTTP routes.

The contract defines no authentication: this service is meant to sit behind
a gateway that authenticates callers.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.chat import ChatService, TopicBlockedError
from app.schemas import BlockedResponse, ChatRequest, ChatResponse, GuardrailOut, HealthResponse

router = APIRouter()


def get_chat_service(request: Request) -> ChatService:
    """Fetch the service that ``create_app`` stored on the application."""
    return request.app.state.chat_service


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
    body: ChatRequest, service: Annotated[ChatService, Depends(get_chat_service)]
) -> ChatResponse | JSONResponse:
    """Run one guarded turn of the agent; a blocked topic is refused with 403."""
    try:
        turn = await service.reply(body.message)
    except TopicBlockedError as blocked:
        refusal = BlockedResponse(error=blocked.event.action, guardrail=blocked.event.name)
        return JSONResponse(status_code=403, content=refusal.model_dump())
    return ChatResponse(
        session_id=body.session_id,
        reply=turn.reply,
        guardrails=[GuardrailOut(name=e.name, action=e.action) for e in turn.guardrails],
    )
