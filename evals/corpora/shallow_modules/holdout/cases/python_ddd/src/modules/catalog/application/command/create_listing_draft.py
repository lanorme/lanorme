from lato import Command
from modules.catalog.application import catalog_module
from modules.catalog.domain.entities import Listing
from modules.catalog.domain.events import ListingDraftCreatedEvent
from modules.catalog.domain.repositories import ListingRepository
from seedwork.domain.value_objects import GenericUUID, Money


class CreateListingDraftCommand(Command):
    """Neutral description."""
    neutral_attribute_1: object
    neutral_attribute_2: object
    neutral_attribute_3: object
    neutral_attribute_4: object
    neutral_attribute_5: object


@catalog_module.handler(CreateListingDraftCommand)
async def create_listing_draft(argument_0, argument_1, argument_2):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    value_6 = value_5 + 1
    value_7 = value_6 + 1
    value_8 = value_7 + 1
    value_9 = value_8 + 1
    value_10 = value_9 + 1
    return value_10
