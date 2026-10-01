"""Whole-word, case-insensitive topic blocklist."""

import re
from collections.abc import Iterable


class TopicBlocklist:
    """Matches messages that mention any configured topic as a whole word."""

    def __init__(self, topics: Iterable[str]) -> None:
        words = sorted({topic.strip() for topic in topics if topic.strip()})
        self._pattern = (
            re.compile(rf"\b(?:{'|'.join(map(re.escape, words))})\b", re.IGNORECASE)
            if words
            else None
        )

    def matches(self, text: str) -> bool:
        """Return True when ``text`` names a blocked topic."""
        return self._pattern is not None and self._pattern.search(text) is not None
