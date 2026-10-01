from dataclasses import dataclass
from enum import StrEnum


class SortingOrder(StrEnum):
    neutral_attribute_1 = 0
    neutral_attribute_2 = 0


@dataclass()
class SortingParams:
    neutral_attribute_3: object
    neutral_attribute_4: object
