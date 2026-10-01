from datetime import datetime, timedelta
from modules.bidding.application import bidding_module
from modules.bidding.domain.entities import Listing
from modules.bidding.domain.repositories import ListingRepository
from modules.bidding.domain.value_objects import Seller
from modules.catalog.domain.events import ListingPublishedEvent


@bidding_module.handler(ListingPublishedEvent)
def when_listing_is_published_start_auction(argument_0, argument_1):
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
