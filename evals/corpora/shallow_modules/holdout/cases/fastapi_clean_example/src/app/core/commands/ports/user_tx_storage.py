from abc import abstractmethod
from typing import Protocol
from app.core.common.entities.types_ import UserId
from app.core.common.entities.user import User


class UserTxStorage(Protocol):
    """Neutral description."""
    @abstractmethod
    def neutral_method_1(self, argument_1): ...
    @abstractmethod
    async def neutral_method_2(self, argument_1, *, keyword_0):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        return value_4
