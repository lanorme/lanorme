"""Application factory: ``uv run uvicorn app.main:create_app --factory``."""

from fastapi import FastAPI
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.agent import build_agent
from app.chat import ChatService
from app.guardrails.blocklist import TopicBlocklist
from app.routes import router
from app.settings import Settings


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    """Wire settings, model, agent and guardrails into a FastAPI app.

    Without ``model``, a real chat model is built from ``AGENT_MODEL`` here,
    at app creation, never at import time.
    """
    settings = Settings()
    chat_model = model if model is not None else init_chat_model(settings.model)
    app = FastAPI(title="Guarded deep agent")
    app.state.chat_service = ChatService(
        agent=build_agent(model=chat_model, tool_call_limit=settings.tool_call_limit),
        blocklist=TopicBlocklist(settings.blocked_topics),
        tool_call_limit=settings.tool_call_limit,
    )
    app.include_router(router)
    return app
