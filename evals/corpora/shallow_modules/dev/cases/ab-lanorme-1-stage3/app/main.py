"""Application factory: ``uv run uvicorn app.main:create_app --factory``."""

from fastapi import FastAPI
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.agent import build_agent
from app.chat import ChatService
from app.conversations import InMemoryConversationStore
from app.policies import InMemoryPolicyStore, TenantPolicies, TenantPolicy
from app.policy_routes import router as policy_router
from app.routes import router
from app.session_routes import router as session_router
from app.settings import Settings


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    """Wire settings, model, agent, guardrails, memory and tenant policies into a FastAPI app.

    Without ``model``, a real chat model is built from ``AGENT_MODEL`` here,
    at app creation, never at import time. ``ADMIN_API_KEY`` is read here too.
    """
    settings = Settings()
    chat_model = model if model is not None else init_chat_model(settings.model)
    app = FastAPI(title="Guarded deep agent")
    app.state.conversations = InMemoryConversationStore()
    app.state.chat_service = ChatService(
        agent=build_agent(model=chat_model), conversations=app.state.conversations
    )
    app.state.tenant_policies = TenantPolicies(
        store=InMemoryPolicyStore(),
        default=TenantPolicy(
            blocked_topics=settings.blocked_topics,
            redact_pii=True,
            max_tool_calls=settings.tool_call_limit,
            system_prompt=None,
        ),
    )
    app.state.admin_api_key = settings.admin_api_key
    app.include_router(router)
    app.include_router(session_router)
    app.include_router(policy_router)
    return app
