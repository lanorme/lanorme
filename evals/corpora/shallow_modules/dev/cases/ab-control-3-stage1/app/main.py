"""FastAPI entry point: `uv run uvicorn app.main:create_app --factory`."""

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
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
    turn_budget,
)

logger = logging.getLogger(__name__)


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


def _final_reply(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message.text
    return ""


def create_app(model: BaseChatModel | None = None, *, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if model is None:
        from langchain.chat_models import init_chat_model

        model = init_chat_model(settings.model)

    agent = build_agent(model, settings)
    blocklist = TopicBlocklist(settings.blocked_topics)

    app = FastAPI(title="Guarded deep agent")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest):
        events: list[GuardrailEvent] = []

        if blocklist.find(request.message):
            return JSONResponse(
                status_code=403,
                content={"error": "blocked", "guardrail": TOPIC_BLOCKLIST},
            )

        message, redacted_in = redact_pii(request.message)

        with turn_budget(settings.tool_call_limit) as budget:
            try:
                result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})
            except Exception:
                logger.exception("agent failed for session %s", request.session_id)
                return JSONResponse(status_code=502, content={"error": "agent_error"})

        if budget.exceeded:
            reply = LIMIT_REACHED_REPLY.format(limit=settings.tool_call_limit)
            events.append(GuardrailEvent(name=TOOL_CALL_LIMIT, action="stopped"))
        else:
            reply = _final_reply(result["messages"])

        reply, redacted_out = redact_pii(reply)
        if redacted_in or redacted_out:
            events.insert(0, GuardrailEvent(name=PII_REDACTION, action="redacted"))

        return ChatResponse(session_id=request.session_id, reply=reply, guardrails=events)

    return app
