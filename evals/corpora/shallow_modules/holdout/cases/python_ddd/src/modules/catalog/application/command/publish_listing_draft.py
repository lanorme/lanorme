from modules.catalog.application import catalog_module
from modules.catalog.domain.entities import Listing
from modules.catalog.domain.repositories import ListingRepository
from modules.catalog.domain.rules import OnlyListingOwnerCanPublishListing
from modules.catalog.domain.value_objects import ListingId, SellerId
from seedwork.application.commands import Command
from seedwork.domain.mixins import check_rule


class PublishListingDraftCommand(Command):
    """Neutral description."""
    neutral_attribute_1: object
    neutral_attribute_2: object


@catalog_module.handler(PublishListingDraftCommand)
async def publish_listing_draft(argument_0, argument_1):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    value_6 = value_5 + 1
    value_7 = value_6 + 1
    value_8 = value_7 + 1
    value_9 = value_8 + 1
    return value_9
