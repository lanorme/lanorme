"""FastAPI service exposing a guarded deep agent."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.agent import GuardedAgent, build_model
from app.config import Settings
from app.guardrails import TOPIC_BLOCKLIST

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


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    settings = Settings.from_env()
    if model is None:
        model = build_model(settings)
    agent = GuardedAgent(model, settings)

    app = FastAPI(title="Deep agent service")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={403: {"description": "Blocked by a guardrail"}, 502: {"description": "Agent failure"}},
    )
    async def chat(request: ChatRequest) -> ChatResponse | JSONResponse:
        try:
            result = await agent.run_turn(request.message)
        except Exception:
            logger.exception("agent turn failed for session %s", request.session_id)
            return JSONResponse(status_code=502, content={"error": "agent_error"})
        if result.blocked:
            return JSONResponse(status_code=403, content={"error": "blocked", "guardrail": TOPIC_BLOCKLIST})
        return ChatResponse(
            session_id=request.session_id,
            reply=result.reply,
            guardrails=[GuardrailAction(**g) for g in result.guardrails],
        )

    return app
