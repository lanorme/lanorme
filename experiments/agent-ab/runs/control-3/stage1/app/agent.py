"""Builds the deepagents agent with the tool-call guardrail wired in."""

from deepagents import create_deep_agent
from deepagents.middleware.subagents import (
    DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
    DEFAULT_SUBAGENT_PROMPT,
)
from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.guardrails import ToolCallLimitGuard
from app.tools import CUSTOM_TOOLS


def build_agent(model: BaseChatModel, settings: Settings):
    guard = ToolCallLimitGuard()
    # The default general-purpose subagent does not inherit the main agent's
    # middleware, so tool calls made through `task` would escape the limit.
    # Redefine it with the same guard; both draw on one per-turn budget.
    general_purpose = {
        "name": "general-purpose",
        "description": DEFAULT_GENERAL_PURPOSE_DESCRIPTION,
        "system_prompt": DEFAULT_SUBAGENT_PROMPT,
        "middleware": [guard],
    }
    return create_deep_agent(
        model=model,
        tools=CUSTOM_TOOLS,
        system_prompt=settings.system_prompt,
        middleware=[guard],
        subagents=[general_purpose],
    )
