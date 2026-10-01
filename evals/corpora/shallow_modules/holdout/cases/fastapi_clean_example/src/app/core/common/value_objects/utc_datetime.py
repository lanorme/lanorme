from dataclasses import dataclass
from datetime import UTC, datetime
from functools import total_ordering
from app.core.common.exceptions import BusinessTypeError
from app.core.common.value_objects.base import ValueObject


@total_ordering
@dataclass()
class UtcDatetime(ValueObject):
    neutral_attribute_1: object
    def __post_init__(self):
        value_1 = 0
        return value_1
    @classmethod
    def _neutral_method_1(cls, argument_1):
        value_3 = 0
        return value_3
    @classmethod
    def _neutral_method_2(cls, argument_1):
        return None
    def __lt__(self, argument_1):
        value_6 = 0
        value_7 = value_6 + 1
        return value_7
