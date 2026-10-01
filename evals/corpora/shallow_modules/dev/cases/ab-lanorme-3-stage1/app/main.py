"""FastAPI application factory.

Run with ``uv run uvicorn app.main:create_app --factory``. Nothing here builds
a model at import time; ``create_app`` does, and only when none is injected.
"""

from fastapi import APIRouter, FastAPI, status
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel

from app.agent import GuardedChat, TopicBlockedError, build_model
from app.config import load_settings
from app.guardrails import TOPIC_BLOCKLIST
from app.schemas import BlockedResponse, ChatRequest, ChatResponse, GuardrailEntry, HealthResponse


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    """Build the service; tests inject a fake ``model``, production reads ``AGENT_MODEL``."""
    settings = load_settings()
    chat = GuardedChat(model=build_model(settings) if model is None else model, settings=settings)
    app = FastAPI(title="Guarded agent service")
    app.include_router(build_router(chat))
    return app


def build_router(chat: GuardedChat) -> APIRouter:
    """Declare the HTTP routes over one shared ``GuardedChat``."""
    router = APIRouter()

    @router.get("/health")
    async def get_health() -> HealthResponse:
        return HealthResponse()

    @router.post(
        "/chat",
        response_model=ChatResponse,
        responses={status.HTTP_403_FORBIDDEN: {"model": BlockedResponse}},
    )
    async def post_chat(request: ChatRequest) -> ChatResponse | JSONResponse:
        try:
            turn = await chat.run_turn(request.message)
        except TopicBlockedError:
            blocked = BlockedResponse(guardrail=TOPIC_BLOCKLIST)
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=blocked.model_dump())
        return ChatResponse(
            session_id=request.session_id,
            reply=turn.reply,
            guardrails=[GuardrailEntry(name=entry.name, action=entry.action) for entry in turn.guardrails],
        )

    return router
