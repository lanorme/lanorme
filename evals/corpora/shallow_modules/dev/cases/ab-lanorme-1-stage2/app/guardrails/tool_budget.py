"""A per-turn tool-call budget shared by the agent and every subagent it spawns.

LangChain's ``ToolCallLimitMiddleware`` keeps its count in graph state, so the
deepagents ``task`` subagent, which runs its own graph, starts again from zero.
This budget lives in a context variable instead: each turn opens a fresh
counter with its own limit (tenants differ), and the main agent and its
subagents all draw from it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import override

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime


class ToolCallLimitExceededError(Exception):
    """The turn asked for more tool calls than its budget allows."""


@dataclass(slots=True)
class TurnToolCalls:
    """Tool calls requested so far in one turn, against that turn's limit."""

    limit: int
    requested: int = 0
    exceeded: bool = False


_current_turn: ContextVar[TurnToolCalls | None] = ContextVar("current_turn", default=None)


@contextmanager
def open_turn(*, limit: int) -> Iterator[TurnToolCalls]:
    """Give the code inside the block a fresh, shared counter allowing ``limit`` calls."""
    calls = TurnToolCalls(limit=limit)
    token = _current_turn.set(calls)
    try:
        yield calls
    finally:
        _current_turn.reset(token)


class ToolCallBudgetMiddleware(AgentMiddleware):
    """Stops the run, before any tool executes, once the turn exceeds its limit."""

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> None:
        """Add the latest model message's tool calls to the turn; raise once over the limit."""
        calls = _current_turn.get()
        if calls is None:
            raise RuntimeError("tool-call budget used outside open_turn()")
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return
        calls.requested += len(last.tool_calls)
        if calls.requested > calls.limit:
            calls.exceeded = True
            raise ToolCallLimitExceededError(f"{calls.requested} tool calls > limit {calls.limit}")
