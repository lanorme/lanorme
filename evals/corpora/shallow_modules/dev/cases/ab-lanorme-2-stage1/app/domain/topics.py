"""Case-insensitive, whole-word matching of blocked topics."""

import re
from collections.abc import Iterable


class TopicBlocklist:
    """The set of topics a message must not mention."""

    def __init__(self, *, topics: Iterable[str]) -> None:
        cleaned = [topic.strip() for topic in topics if topic.strip()]
        self._pattern = _compile_topics(cleaned) if cleaned else None

    def find_topic(self, text: str) -> str | None:
        """Return the first blocked topic the text mentions, as written in the text."""
        if self._pattern is None:
            return None
        match = self._pattern.search(text)
        return match.group(0) if match else None


def _compile_topics(topics: list[str]) -> re.Pattern[str]:
    # Lookarounds rather than \b so a topic such as "c++" still has edges,
    # and any run of whitespace matches the gaps in a multi-word topic.
    alternatives = (r"\s+".join(map(re.escape, topic.split())) for topic in topics)
    return re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)", re.IGNORECASE)
