"""Construction of the deep agent and its tool-call budget."""

from deepagents import create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from app.guardrails.tool_budget import ToolCallBudgetMiddleware
from app.tools import AGENT_TOOLS

SYSTEM_PROMPT = """\
You are a helpful assistant. Use the calculator tool for arithmetic and the
current_time tool when asked about the date or time. Keep answers concise.
Placeholders such as [REDACTED_EMAIL] or [REDACTED_PHONE] stand for personal
details that were removed on purpose; never try to guess or reconstruct them."""


def build_agent(*, model: BaseChatModel, tool_call_limit: int) -> CompiledStateGraph:
    """Build a stateless deep agent whose turn aborts once it asks for too many tool calls.

    The general-purpose subagent is declared explicitly so it carries the same
    budget middleware; its calls then count against the same per-turn total.
    """
    budget = ToolCallBudgetMiddleware(limit=tool_call_limit)
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
        middleware=[budget],
        subagents=[general_purpose],
    )
