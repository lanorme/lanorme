from seedwork.domain.events import DomainEvent
from seedwork.domain.value_objects import GenericUUID, Money


class ListingDraftCreatedEvent(DomainEvent):
    neutral_attribute_1: object


class ListingDraftUpdatedEvent(DomainEvent):
    neutral_attribute_2: object


class ListingDraftDeletedEvent(DomainEvent):
    neutral_attribute_3: object


class ListingPublishedEvent(DomainEvent):
    neutral_attribute_4: object
    neutral_attribute_5: object
    neutral_attribute_6: object
