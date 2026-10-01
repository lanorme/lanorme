"""Guardrails applied around the agent.

* PII redaction  - pure text transform on the user message and the reply.
* Topic blocklist - pure check on the user message, before the model is called.
* Tool-call limit - agent middleware that counts tool calls per turn, across
  the main agent and its subagents, and ends the turn when the budget is spent.

Per-tenant policies (app/policies.py) choose the topics, whether PII is
redacted and the tool-call limit for each request. A policy's system prompt is
added to the agent's instructions by `TenantInstructionsMiddleware`.
"""

import re
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

PII_REDACTION = "pii_redaction"
TOPIC_BLOCKLIST = "topic_blocklist"
TOOL_CALL_LIMIT = "tool_call_limit"

# --------------------------------------------------------------------------- PII

EMAIL_TOKEN = "[REDACTED_EMAIL]"
PHONE_TOKEN = "[REDACTED_PHONE]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Phone numbers, as a few explicit shapes rather than one loose "digit groups"
# pattern: a loose pattern greedily merges neighbouring numbers (a date next to
# a phone number) into one over-long candidate, which then fails validation and
# leaks the phone number.
_SEP = r"[\s.-]?"
_PHONE_PATTERNS = [
    # International: +44 20 7946 0958, +1 (555) 123-4567, +33 1 23 45 67 89
    re.compile(rf"(?<![\w+])\+\d{{1,3}}(?:{_SEP}(?:\(\d{{1,4}}\)|\d{{1,4}})){{2,5}}(?!\w)"),
    # North American: (555) 123-4567, 555-123-4567, 555.123.4567, 5551234567
    re.compile(rf"(?<![\w+(])(?:\(\d{{3}}\)\s?|\d{{3}}{_SEP})\d{{3}}{_SEP}\d{{4}}(?!\w)"),
    # National with trunk prefix: 020 7946 0958, (020) 7946 0958, 0161-496-0000
    re.compile(rf"(?<![\w+(])(?:\(0\d{{1,4}}\)|0\d{{1,4}}){_SEP}\d{{3,4}}{_SEP}\d{{3,4}}(?!\w)"),
]
_INTL_MIN_DIGITS = 8
_INTL_MAX_DIGITS = 15
_GROUP_END_RE = re.compile(r"[\d)](?=[\s.-][\d(])")


def _digit_count(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def _redact_international(text: str) -> str:
    """Redact +-prefixed numbers, trimming trailing groups that make them too long."""
    out, pos = [], 0
    for match in _PHONE_PATTERNS[0].finditer(text):
        candidate = match.group(0)
        # Longest prefix (ending on a group boundary) with a plausible digit count.
        cuts = [m.end() for m in _GROUP_END_RE.finditer(candidate)] + [len(candidate)]
        for cut in sorted(cuts, reverse=True):
            if _INTL_MIN_DIGITS <= _digit_count(candidate[:cut]) <= _INTL_MAX_DIGITS:
                out += [text[pos : match.start()], PHONE_TOKEN]
                pos = match.start() + cut
                break
    out.append(text[pos:])
    return "".join(out)


def redact_pii(text: str) -> tuple[str, bool]:
    """Replace emails and phone numbers. Returns (redacted_text, changed)."""
    redacted = _EMAIL_RE.sub(EMAIL_TOKEN, text)
    redacted = _redact_international(redacted)
    for pattern in _PHONE_PATTERNS[1:]:
        redacted = pattern.sub(PHONE_TOKEN, redacted)
    return redacted, redacted != text


# --------------------------------------------------------------- Topic blocklist


class TopicBlocklist:
    """Case-insensitive whole-word matcher for a list of blocked topics."""

    def __init__(self, topics: Iterable[str]):
        self.topics = tuple(t for t in (t.strip() for t in topics) if t)
        self._pattern = (
            re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(t) for t in self.topics) + r")(?!\w)",
                re.IGNORECASE,
            )
            if self.topics
            else None
        )

    def find(self, text: str) -> str | None:
        """Return the first blocked topic mentioned in `text`, or None."""
        if self._pattern is None:
            return None
        match = self._pattern.search(text)
        return match.group(0) if match else None


# --------------------------------------------------------------- Tool-call limit

LIMIT_REACHED_REPLY = (
    "I had to stop here: this request reached the limit of {limit} tool calls "
    "per turn. Please narrow the request or split it into smaller steps."
)


@dataclass
class TurnBudget:
    """Mutable per-turn tool-call budget, shared by the main agent and subagents."""

    limit: int
    used: int = 0
    exceeded: bool = False


_current_budget: ContextVar[TurnBudget | None] = ContextVar("tool_call_budget", default=None)


@contextmanager
def turn_budget(limit: int):
    """Install a fresh budget for the duration of one chat turn."""
    budget = TurnBudget(limit=limit)
    token = _current_budget.set(budget)
    try:
        yield budget
    finally:
        _current_budget.reset(token)


class ToolCallLimitGuard(AgentMiddleware):
    """Stops the turn once the model asks for more tool calls than the budget allows.

    The budget lives in a ContextVar set per request, so one middleware instance
    is safe to share between concurrent requests and between the main agent and
    its subagents (whose tool calls count against the same budget).

    If a batch of tool calls would take the turn past the limit, none of that
    batch runs: each pending call gets an error ToolMessage (to keep the history
    valid) and the agent jumps to the end. The before_model hook also ends any
    agent that resumes after a subagent hit the limit.
    """

    @property
    def name(self) -> str:
        return "ToolCallLimitGuard"

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        budget = _current_budget.get()
        if budget is not None and budget.exceeded:
            return {"jump_to": "end"}
        return None

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        budget = _current_budget.get()
        messages = state.get("messages", [])
        if budget is None or not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return None

        if budget.used + len(last.tool_calls) <= budget.limit:
            budget.used += len(last.tool_calls)
            return None

        budget.exceeded = True
        stopped = [
            ToolMessage(
                content="Tool call limit reached for this turn; call not executed.",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
            for call in last.tool_calls
        ]
        final = AIMessage(content=LIMIT_REACHED_REPLY.format(limit=budget.limit))
        return {"messages": [*stopped, final], "jump_to": "end"}


# ----------------------------------------------------------- Tenant instructions

_current_instructions: ContextVar[str | None] = ContextVar("tenant_instructions", default=None)


@contextmanager
def tenant_instructions(text: str | None):
    """Set the calling tenant's extra instructions for the duration of one turn."""
    token = _current_instructions.set(text)
    try:
        yield
    finally:
        _current_instructions.reset(token)


class TenantInstructionsMiddleware(AgentMiddleware):
    """Appends the calling tenant's system prompt to the agent's instructions.

    Reads a per-request ContextVar (like the tool-call budget), so one agent
    serves every tenant and the main agent and its subagents both get the
    tenant's instructions.
    """

    @property
    def name(self) -> str:
        return "TenantInstructionsMiddleware"

    @staticmethod
    def _with_instructions(request: Any) -> Any:
        text = _current_instructions.get()
        if not text:
            return request
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": f"\n\n{text}" if blocks else text})
        return request.override(system_message=SystemMessage(content_blocks=blocks))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._with_instructions(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._with_instructions(request))
