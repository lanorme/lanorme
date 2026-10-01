"""FastAPI application factory.

Run with ``uv run uvicorn app.main:create_app --factory``. Nothing here builds
a model at import time; ``create_app`` does, and only when none is injected.
"""

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel

from app.agent import GuardedChat, build_model
from app.api.admin_auth import build_admin_key_check
from app.api.chat import build_chat_router
from app.api.sessions import build_session_router
from app.api.tenants import build_tenant_router
from app.config import load_settings
from app.conversations import ConversationStore, InMemoryConversationStore
from app.policies import InMemoryPolicyStore, PolicyStore, TenantPolicies, build_default_policy


def create_app(
    model: BaseChatModel | None = None,
    *,
    policy_store: PolicyStore | None = None,
    conversation_store: ConversationStore | None = None,
) -> FastAPI:
    """Build the service; tests inject a fake ``model``, production reads ``AGENT_MODEL``.

    Policies and conversations live in memory unless a ``policy_store`` or a
    ``conversation_store`` is given.
    """
    settings = load_settings()
    conversations = InMemoryConversationStore() if conversation_store is None else conversation_store
    chat = GuardedChat(model=build_model(settings) if model is None else model, conversations=conversations)
    policies = TenantPolicies(
        store=InMemoryPolicyStore() if policy_store is None else policy_store,
        default=build_default_policy(settings),
    )
    app = FastAPI(title="Guarded agent service")
    app.include_router(build_chat_router(chat=chat, policies=policies))
    app.include_router(build_session_router(conversations=conversations))
    app.include_router(
        build_tenant_router(policies=policies, require_admin_key=build_admin_key_check(settings.admin_api_key))
    )
    return app
