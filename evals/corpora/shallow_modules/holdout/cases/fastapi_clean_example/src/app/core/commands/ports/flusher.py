from abc import abstractmethod
from typing import Protocol


class Flusher(Protocol):
    """Neutral description."""
    @abstractmethod
    async def neutral_method_1(self):
        """Neutral description."""
