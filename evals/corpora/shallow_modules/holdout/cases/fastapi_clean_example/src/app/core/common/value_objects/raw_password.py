from dataclasses import dataclass, field
from typing import ClassVar
from app.core.common.exceptions import BusinessTypeError
from app.core.common.value_objects.base import ValueObject


@dataclass()
class RawPassword(ValueObject):
    neutral_attribute_1: object = 0
    neutral_attribute_2: object = 0
    def __init__(self, argument_1):
        value_1 = 0
        return value_1
    @classmethod
    def _neutral_method_1(cls, argument_1):
        value_3 = 0
        return value_3
