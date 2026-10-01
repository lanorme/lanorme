"""Builds the deepagents agent with the tool-call guardrail and tenant instructions wired in."""

from deepagents import create_deep_agent
from deepagents.middleware.subagents import (
    DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
    DEFAULT_SUBAGENT_PROMPT,
)
from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.guardrails import TenantInstructionsMiddleware, ToolCallLimitGuard
from app.tools import CUSTOM_TOOLS


def build_agent(model: BaseChatModel, settings: Settings):
    # Both middlewares read per-request ContextVars, so one agent serves every
    # tenant. The default general-purpose subagent does not inherit the main
    # agent's middleware, so tool calls made through `task` would escape the
    # limit and the subagent would miss the tenant's instructions. Redefine it
    # with the same middleware; both agents draw on one per-turn budget.
    middleware = [TenantInstructionsMiddleware(), ToolCallLimitGuard()]
    general_purpose = {
        "name": "general-purpose",
        "description": DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
        "system_prompt": DEFAULT_SUBAGENT_PROMPT,
        "middleware": middleware,
    }
    return create_deep_agent(
        model=model,
        tools=CUSTOM_TOOLS,
        system_prompt=settings.system_prompt,
        middleware=middleware,
        subagents=[general_purpose],
    )
