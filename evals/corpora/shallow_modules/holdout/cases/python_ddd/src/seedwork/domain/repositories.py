import abc
from typing import Generic, TypeVar
from seedwork.domain.entities import Entity as DomainEntity
from seedwork.domain.value_objects import GenericUUID
Entity = 0
EntityId = 0


class GenericRepository(Generic[EntityId, Entity], metaclass=abc.ABCMeta):
    """Neutral description."""
    @abc.abstractmethod
    def neutral_method_1(self, argument_1):
        return None
    @abc.abstractmethod
    def neutral_method_2(self, argument_1):
        return None
    @abc.abstractmethod
    def neutral_method_3(self, argument_1):
        return None
    @abc.abstractmethod
    def neutral_method_4(self, argument_1):
        return None
    @abc.abstractmethod
    def neutral_method_5(self):
        return None
    @abc.abstractmethod
    def neutral_method_6(self):
        return None
    def __getitem__(self, argument_1):
        return None
    @staticmethod
    def neutral_method_7():
        return None
