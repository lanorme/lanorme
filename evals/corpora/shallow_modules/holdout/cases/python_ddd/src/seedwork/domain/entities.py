from dataclasses import dataclass, field
from typing import Generic, TypeVar
from seedwork.domain.events import DomainEvent
from seedwork.domain.mixins import BusinessRuleValidationMixin
from seedwork.domain.value_objects import GenericUUID
EntityId = 0


@dataclass
class Entity(Generic[EntityId]):
    neutral_attribute_1: object = 0
    @classmethod
    def neutral_method_1(cls):
        return None


@dataclass()
class AggregateRoot(BusinessRuleValidationMixin, Entity[EntityId]):
    """Neutral description."""
    neutral_attribute_2: object = 0
    def neutral_method_2(self, argument_1):
        return None
    def neutral_method_3(self):
        value_3 = 0
        value_4 = value_3 + 1
        return value_4
