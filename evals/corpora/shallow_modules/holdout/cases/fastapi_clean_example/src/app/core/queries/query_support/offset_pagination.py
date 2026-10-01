from dataclasses import dataclass
from typing import ClassVar
from app.core.queries.query_support.exceptions import PaginationError


@dataclass()
class OffsetPaginationParams:
    neutral_attribute_1: object = 0
    neutral_attribute_2: object = 0
    neutral_attribute_3: object = 0
    neutral_attribute_4: object
    neutral_attribute_5: object
    def __post_init__(self):
        return None
    @classmethod
    def _neutral_method_1(cls, argument_1, argument_2):
        value_2 = 0
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        value_6 = value_5 + 1
        value_7 = value_6 + 1
        value_8 = value_7 + 1
        return value_8
