"""HTTP endpoints: health check and guarded chat."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.api.schemas import ChatRequest, ChatResponse, ErrorResponse, GuardrailOut, HealthResponse
from app.application.services.chat import ChatService
from app.domain.errors import AgentUnavailableError, TopicBlockedError
from app.domain.guardrails import TOPIC_BLOCKED

router = APIRouter()


def get_chat_service(request: Request) -> ChatService:
    """Return the chat service that create_app stored on the application state."""
    return request.app.state.chat_service


@router.get("/health")
async def get_health() -> HealthResponse:
    """Report that the service is up."""
    return HealthResponse(status="ok")


# The contract fixes /chat as unauthenticated; callers authenticate upstream.
@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
async def post_chat(  # lanorme: ignore[AUTHN-001]
    body: ChatRequest, service: Annotated[ChatService, Depends(get_chat_service)]
) -> ChatResponse | JSONResponse:
    """Answer one message through the guardrails; 403 when a topic is blocked."""
    try:
        turn = await service.respond(body.message)
    except TopicBlockedError:
        error = ErrorResponse(error=TOPIC_BLOCKED.action, guardrail=TOPIC_BLOCKED.name)
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=error.model_dump())
    except AgentUnavailableError:
        error = ErrorResponse(error="agent_unavailable")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY, content=error.model_dump(exclude_none=True)
        )
    guardrails = [GuardrailOut(name=item.name, action=item.action) for item in turn.guardrails]
    return ChatResponse(session_id=body.session_id, reply=turn.reply, guardrails=guardrails)
