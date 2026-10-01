"""Application factory and composition root."""

from fastapi import FastAPI
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.api import routes, sessions, tenants
from app.application.services.chat import ChatService
from app.application.services.conversations import ConversationService
from app.application.services.policies import PolicyService
from app.domain.policy import GuardrailPolicy
from app.infrastructure.agent_tools import AGENT_TOOLS
from app.infrastructure.repositories.in_memory_conversations import (
    InMemoryConversationRepository,
)
from app.infrastructure.repositories.in_memory_policies import InMemoryPolicyRepository
from app.infrastructure.services.deep_agent import DeepAgentChat
from app.infrastructure.settings import Settings


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    """Build the service; without a model, one is built from AGENT_MODEL."""
    settings = Settings()
    chat_model = model if model is not None else init_chat_model(settings.agent_model)
    agent = DeepAgentChat(model=chat_model, tools=AGENT_TOOLS)
    default_policy = GuardrailPolicy(
        blocked_topics=settings.blocked_topics,
        redact_pii=True,
        max_tool_calls=settings.max_tool_calls,
    )
    policies = PolicyService(repository=InMemoryPolicyRepository(), default=default_policy)
    conversations = InMemoryConversationRepository()

    app = FastAPI(title="Guarded agent")
    app.state.chat_service = ChatService(
        agent=agent, policies=policies, conversations=conversations
    )
    app.state.conversation_service = ConversationService(repository=conversations)
    app.state.policy_service = policies
    app.state.admin_api_key = _encode_admin_key(settings)
    app.include_router(routes.router)
    app.include_router(sessions.router)
    app.include_router(tenants.router)
    return app


def _encode_admin_key(settings: Settings) -> bytes | None:
    # An empty ADMIN_API_KEY counts as unset, so it can never match an empty header.
    if settings.admin_api_key is None:
        return None
    return settings.admin_api_key.get_secret_value().encode() or None
