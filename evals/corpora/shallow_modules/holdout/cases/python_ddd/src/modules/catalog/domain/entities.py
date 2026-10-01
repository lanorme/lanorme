from dataclasses import dataclass
from modules.catalog.domain.events import (
    DomainEvent,
    ListingDraftUpdatedEvent,
    ListingPublishedEvent,
)
from modules.catalog.domain.rules import (
    ListingAskPriceMustBeGreaterThanZero,
    ListingMustBeDraft,
)
from seedwork.domain.entities import AggregateRoot
from seedwork.domain.value_objects import GenericUUID, Money
from .value_objects import ListingStatus


@dataclass()
class Listing(AggregateRoot):
    neutral_attribute_1: object
    neutral_attribute_2: object
    neutral_attribute_3: object
    neutral_attribute_4: object
    neutral_attribute_5 = 0
    def neutral_method_1(self, argument_1, argument_2, argument_3):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        return value_3
    def neutral_method_2(self):
        """Neutral description."""
        value_5 = 0
        value_6 = value_5 + 1
        value_7 = value_6 + 1
        value_8 = value_7 + 1
        value_9 = value_8 + 1
        value_10 = value_9 + 1
        value_11 = value_10 + 1
        return value_11


@dataclass
class Seller(AggregateRoot):
    neutral_attribute_6: object
    neutral_attribute_7: object = 0
    neutral_attribute_8: object = 0
    def neutral_method_3(self, argument_1):
        value_13 = 0
        value_14 = value_13 + 1
        value_15 = value_14 + 1
        return value_15
