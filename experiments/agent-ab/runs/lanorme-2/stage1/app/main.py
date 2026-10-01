"""Application factory and composition root."""

from fastapi import FastAPI
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.api.routes import router
from app.application.services.chat import ChatService
from app.domain.topics import TopicBlocklist
from app.infrastructure.agent_tools import AGENT_TOOLS
from app.infrastructure.services.deep_agent import DeepAgentChat
from app.infrastructure.settings import Settings


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    """Build the service; without a model, one is built from AGENT_MODEL."""
    settings = Settings()
    chat_model = model if model is not None else init_chat_model(settings.agent_model)
    agent = DeepAgentChat(
        model=chat_model, tools=AGENT_TOOLS, max_tool_calls=settings.max_tool_calls
    )
    blocklist = TopicBlocklist(topics=settings.blocked_topics)

    app = FastAPI(title="Guarded agent")
    app.state.chat_service = ChatService(agent=agent, blocklist=blocklist)
    app.include_router(router)
    return app
