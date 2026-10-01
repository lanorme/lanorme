"""Guardrails: PII redaction, topic blocklist and a per-turn tool-call limit."""

from __future__ import annotations

import re
import threading
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware

PII_REDACTION = "pii_redaction"
TOPIC_BLOCKLIST = "topic_blocklist"
TOOL_CALL_LIMIT = "tool_call_limit"

REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_PHONE = "[REDACTED_PHONE]"

# --- PII redaction ---------------------------------------------------------

_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")

# Candidate phone numbers: optional +country code, an area code (optionally in
# parentheses), then groups of digits separated by at most one space, dot or
# hyphen. Candidates are then checked by digit count in `_is_phone`.
_PHONE_RE = re.compile(
    r"(?<![\w+])"
    r"(?P<intl>\+\d{1,3}[ .-]?)?"
    r"(?:\(\d{1,4}\)|\d{1,4})"
    r"(?:[ .-]?\d{2,4}){1,4}"
    r"(?![\w])"
)


def _is_phone(match: re.Match[str]) -> bool:
    digits = sum(c.isdigit() for c in match.group())
    if match.group("intl"):
        return 8 <= digits <= 15
    # Without a country code, require a full national number (e.g. 10-digit
    # North American or 11-digit UK numbers). This keeps dates, years, prices
    # and short numbers from being redacted.
    return 10 <= digits <= 11


def redact_pii(text: str) -> tuple[str, bool]:
    """Replace emails and phone numbers. Returns the new text and whether anything changed."""
    redacted = _EMAIL_RE.sub(REDACTED_EMAIL, text)
    redacted = _PHONE_RE.sub(lambda m: REDACTED_PHONE if _is_phone(m) else m.group(), redacted)
    return redacted, redacted != text


# --- Topic blocklist -------------------------------------------------------


class TopicBlocklist:
    """Case-insensitive whole-word match against a list of topics (words or phrases)."""

    def __init__(self, topics: Iterable[str]) -> None:
        self.topics = tuple(t.strip() for t in topics if t.strip())
        if self.topics:
            alternatives = "|".join(
                r"\s+".join(re.escape(word) for word in topic.split())
                for topic in sorted(self.topics, key=len, reverse=True)
            )
            self._pattern: re.Pattern[str] | None = re.compile(
                rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE
            )
        else:
            self._pattern = None

    def find(self, text: str) -> str | None:
        """Return the first blocked topic mentioned in `text`, or None."""
        if self._pattern is None:
            return None
        match = self._pattern.search(text)
        return match.group() if match else None


# --- Tool-call limit -------------------------------------------------------


class ToolCallLimitExceeded(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"tool call limit of {limit} exceeded")
        self.limit = limit


class _TurnBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise ToolCallLimitExceeded(self.limit)
            self.used += 1


_current_budget: ContextVar[_TurnBudget | None] = ContextVar("tool_call_budget", default=None)


@contextmanager
def tool_call_budget(limit: int) -> Iterator[_TurnBudget]:
    """Scope a tool-call budget to one turn.

    The budget lives in a ContextVar holding a mutable counter, so tool calls
    made by subagents (which run inside the parent's `task` tool, in the same
    context) draw from the same budget as the main agent.
    """
    budget = _TurnBudget(limit)
    token = _current_budget.set(budget)
    try:
        yield budget
    finally:
        _current_budget.reset(token)


class TurnToolCallLimitMiddleware(AgentMiddleware):
    """Raises `ToolCallLimitExceeded` before running a tool call past the turn's budget.

    The exception is not caught by the tool node, so it ends the whole turn,
    including any subagent run that is in progress.
    """

    name = "TurnToolCallLimitMiddleware"

    def _take(self) -> None:
        budget = _current_budget.get()
        if budget is not None:
            budget.take()

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        self._take()
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        self._take()
        return await handler(request)
