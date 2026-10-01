"""FastAPI entry point: ``uv run uvicorn app.main:create_app --factory``."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.agent import BlockedTopicError, GuardedAgent, build_model
from app.config import get_settings
from app.guardrails import BLOCKLIST_EVENT


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


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    settings = get_settings()
    agent = GuardedAgent(model if model is not None else build_model(settings), settings)

    app = FastAPI(title="Guarded deep agent")
    app.state.agent = agent

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={403: {"description": "Message blocked by a guardrail"}},
    )
    async def chat(request: ChatRequest) -> ChatResponse | JSONResponse:
        try:
            result = await agent.run_turn(request.message)
        except BlockedTopicError:
            return JSONResponse(
                status_code=403,
                content={"error": "blocked", "guardrail": BLOCKLIST_EVENT.name},
            )
        return ChatResponse(
            session_id=request.session_id,
            reply=result.reply,
            guardrails=[GuardrailOut(name=e.name, action=e.action) for e in result.guardrails],
        )

    return app
