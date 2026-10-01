"""FastAPI entry point: ``uv run uvicorn app.main:create_app --factory``."""

import hmac
import os
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.agent import BlockedTopicError, GuardedAgent, build_model
from app.config import get_settings
from app.guardrails import BLOCKLIST_EVENT
from app.policies import (
    DEFAULT_TENANT,
    InMemoryPolicyStore,
    PolicyService,
    TenantPolicy,
    default_policy,
)


async def _read_policy(request: Request) -> TenantPolicy:
    """Parse a PUT body into a policy, as FastAPI would, but only once called.

    Declaring the body as a parameter would make FastAPI reject malformed JSON
    with 422 before the admin-key check runs; unauthenticated callers get 401.
    """
    try:
        raw = await request.json()
    except ValueError as exc:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": "JSON decode error", "input": {},
              "ctx": {"error": str(exc)}}]
        ) from exc
    try:
        return TenantPolicy.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors(include_url=False)]
        ) from exc


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
    policies = PolicyService(InMemoryPolicyStore(), default_policy(settings))
    # Unset or empty means no key can match, so the admin endpoints stay closed.
    admin_key = os.environ.get("ADMIN_API_KEY", "")

    def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        if not admin_key or x_admin_key is None or not hmac.compare_digest(
            x_admin_key.encode(), admin_key.encode()
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin key")

    app = FastAPI(title="Guarded deep agent")
    app.state.agent = agent
    # Swap ``app.state.policies.store`` for another PolicyStore (e.g. a database).
    app.state.policies = policies

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/chat",
        response_model=ChatResponse,
        responses={403: {"description": "Message blocked by a guardrail"}},
    )
    async def chat(
        request: ChatRequest,
        x_tenant_id: Annotated[str | None, Header()] = None,
    ) -> ChatResponse | JSONResponse:
        policy = await policies.effective(x_tenant_id or DEFAULT_TENANT)
        try:
            result = await agent.run_turn(request.message, policy)
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

    admin = [Depends(require_admin)]
    unauthorized = {401: {"description": "Missing or wrong X-Admin-Key"}}

    @app.get(
        "/tenants/{tenant_id}/policy",
        response_model=TenantPolicy,
        dependencies=admin,
        responses=unauthorized,
    )
    async def get_policy(tenant_id: str) -> TenantPolicy:
        return await policies.effective(tenant_id)

    @app.put(
        "/tenants/{tenant_id}/policy",
        response_model=TenantPolicy,
        dependencies=admin,
        responses=unauthorized,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": TenantPolicy.model_json_schema()}},
            }
        },
    )
    async def put_policy(tenant_id: str, request: Request) -> TenantPolicy:
        policy = await _read_policy(request)
        await policies.store.put(tenant_id, policy)
        return policy

    @app.delete(
        "/tenants/{tenant_id}/policy",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=admin,
        responses=unauthorized,
    )
    async def delete_policy(tenant_id: str) -> Response:
        await policies.store.delete(tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
