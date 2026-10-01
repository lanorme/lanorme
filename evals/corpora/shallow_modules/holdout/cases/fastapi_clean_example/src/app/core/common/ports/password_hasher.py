from abc import abstractmethod
from typing import Protocol
from app.core.common.entities.types_ import UserPasswordHash
from app.core.common.value_objects.raw_password import RawPassword


class PasswordHasher(Protocol):
    @abstractmethod
    async def neutral_method_1(self, argument_1): ...
    @abstractmethod
    async def neutral_method_2(self, argument_1, argument_2): ...
