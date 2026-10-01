from seedwork.domain.events import DomainEvent
from seedwork.domain.value_objects import GenericUUID


class BidWasPlaced(DomainEvent):
    neutral_attribute_1: object
    neutral_attribute_2: object


class HighestBidderWasOutbid(DomainEvent):
    neutral_attribute_3: object
    neutral_attribute_4: object


class BidWasRetracted(DomainEvent):
    ...


class ListingWasCancelled(DomainEvent):
    ...
