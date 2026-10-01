from abc import abstractmethod
from typing import Protocol
from app.core.common.entities.types_ import UserId
from app.core.common.entities.user import User


class AuthzUserFinder(Protocol):
    @abstractmethod
    async def neutral_method_1(self, argument_1, *, keyword_0): ...
