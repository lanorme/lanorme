from datetime import datetime
from dataclasses import dataclass, field
from seedwork.domain.value_objects import GenericUUID, Money, ValueObject


@dataclass()
class Bidder(ValueObject):
    neutral_attribute_1: object


@dataclass()
class Seller(ValueObject):
    neutral_attribute_2: object


@dataclass()
class Bid(ValueObject):
    neutral_attribute_3: object
    neutral_attribute_4: object
    neutral_attribute_5: object = 0
