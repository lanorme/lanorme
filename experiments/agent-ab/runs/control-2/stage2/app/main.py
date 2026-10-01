"""FastAPI service exposing a guarded deep agent."""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.agent import GuardedAgent, build_model
from app.config import Settings
from app.guardrails import TOPIC_BLOCKLIST
from app.policies import DEFAULT_TENANT, InMemoryPolicyStore, PolicyStore, TenantPolicy, effective_policy

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


def tenant_id_from_header(x_tenant_id: Annotated[str | None, Header()] = None) -> str:
    """The calling tenant: the `X-Tenant-ID` header, or "default" when absent or blank."""
    if x_tenant_id is None or not x_tenant_id.strip():
        return DEFAULT_TENANT
    return x_tenant_id


def policy_store(request: Request) -> PolicyStore:
    return request.app.state.policy_store


Store = Annotated[PolicyStore, Depends(policy_store)]


def admin_router(admin_api_key: str | None, default_policy: TenantPolicy) -> APIRouter:
    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        # Fails closed: with no ADMIN_API_KEY configured, nobody is an admin.
        if (
            not admin_api_key
            or x_admin_key is None
            or not hmac.compare_digest(x_admin_key.encode(), admin_api_key.encode())
        ):
            raise HTTPException(status_code=401, detail="invalid or missing admin key")

    router = APIRouter(prefix="/tenants/{tenant_id}/policy", dependencies=[Depends(require_admin)])

    @router.get("", response_model=TenantPolicy, responses={401: {"description": "Bad admin key"}})
    async def get_policy(tenant_id: str, store: Store) -> TenantPolicy:
        return await effective_policy(store, tenant_id, default_policy)

    # The body is parsed by hand, after `require_admin`, so a request without a
    # valid admin key always gets 401, even when its body is also invalid.
    @router.put(
        "",
        response_model=TenantPolicy,
        responses={401: {"description": "Bad admin key"}},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": TenantPolicy.model_json_schema()}},
            }
        },
    )
    async def put_policy(tenant_id: str, request: Request, store: Store) -> TenantPolicy:
        try:
            policy = TenantPolicy.model_validate_json(await request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False)) from None
        await store.put(tenant_id, policy)
        return policy

    @router.delete("", status_code=204, responses={401: {"description": "Bad admin key"}})
    async def delete_policy(tenant_id: str, store: Store) -> Response:
        await store.delete(tenant_id)
        return Response(status_code=204)

    return router


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    settings = Settings.from_env()
    if model is None:
        model = build_model(settings)
    agent = GuardedAgent(model, settings)

    app = FastAPI(title="Deep agent service")
    # Swap for a database-backed `PolicyStore` to persist policies.
    app.state.policy_store = InMemoryPolicyStore()
    app.include_router(admin_router(settings.admin_api_key, agent.default_policy))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={403: {"description": "Blocked by a guardrail"}, 502: {"description": "Agent failure"}},
    )
    async def chat(
        request: ChatRequest,
        tenant_id: Annotated[str, Depends(tenant_id_from_header)],
        store: Store,
    ) -> ChatResponse | JSONResponse:
        try:
            policy = await effective_policy(store, tenant_id, agent.default_policy)
            result = await agent.run_turn(request.message, policy)
        except Exception:
            logger.exception("agent turn failed for tenant %r session %s", tenant_id, request.session_id)
            return JSONResponse(status_code=502, content={"error": "agent_error"})
        if result.blocked:
            return JSONResponse(status_code=403, content={"error": "blocked", "guardrail": TOPIC_BLOCKLIST})
        return ChatResponse(
            session_id=request.session_id,
            reply=result.reply,
            guardrails=[GuardrailAction(**g) for g in result.guardrails],
        )

    return app
