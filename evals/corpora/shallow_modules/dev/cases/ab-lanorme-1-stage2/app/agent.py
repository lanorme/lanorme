"""Construction of the deep agent, its tool-call budget and per-turn instructions."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import override

from deepagents import create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph.state import CompiledStateGraph

from app.guardrails.tool_budget import ToolCallBudgetMiddleware
from app.tools import AGENT_TOOLS

SYSTEM_PROMPT = """\
You are a helpful assistant. Use the calculator tool for arithmetic and the
current_time tool when asked about the date or time. Keep answers concise.
Placeholders such as [REDACTED_EMAIL] or [REDACTED_PHONE] stand for personal
details that were removed on purpose; never try to guess or reconstruct them."""


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Per-invocation runtime context: what varies by tenant but not by agent."""

    tenant_instructions: str | None = None


ModelHandler = Callable[[ModelRequest], ModelResponse]
AsyncModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]


class TenantInstructionsMiddleware(AgentMiddleware):
    """Appends the turn's tenant instructions to the agent's system prompt.

    The agent is built once and shared by every tenant, so the instructions
    arrive with each invocation as ``TurnContext`` instead of at build time.
    """

    @override
    def wrap_model_call(self, request: ModelRequest, handler: ModelHandler) -> ModelResponse:
        """Call the model with the tenant's instructions added."""
        return handler(_with_tenant_instructions(request))

    @override
    async def awrap_model_call(
        self, request: ModelRequest, handler: AsyncModelHandler
    ) -> ModelResponse:
        """Call the model with the tenant's instructions added."""
        return await handler(_with_tenant_instructions(request))


def _with_tenant_instructions(request: ModelRequest) -> ModelRequest:
    """Return ``request`` with the tenant instructions after the existing system prompt."""
    context = request.runtime.context
    if not isinstance(context, TurnContext) or not context.tenant_instructions:
        return request
    base = request.system_message.content_blocks if request.system_message else []
    extra = {"type": "text", "text": f"\n\n{context.tenant_instructions}"}
    return request.override(system_message=SystemMessage(content_blocks=[*base, extra]))


def build_agent(*, model: BaseChatModel) -> CompiledStateGraph:
    """Build one stateless deep agent that serves every tenant.

    The general-purpose subagent is declared explicitly so it carries the same
    budget middleware; its calls then count against the same per-turn total.
    Tenant instructions apply to the main agent only: they shape the reply the
    tenant's user sees, while a subagent answers the main agent.
    """
    budget = ToolCallBudgetMiddleware()
    general_purpose: SubAgent = {
        **GENERAL_PURPOSE_SUBAGENT,
        "model": model,
        "tools": list(AGENT_TOOLS),
        "middleware": [budget],
    }
    return create_deep_agent(
        model=model,
        tools=list(AGENT_TOOLS),
        system_prompt=SYSTEM_PROMPT,
        middleware=[budget, TenantInstructionsMiddleware()],
        subagents=[general_purpose],
        context_schema=TurnContext,
    )
