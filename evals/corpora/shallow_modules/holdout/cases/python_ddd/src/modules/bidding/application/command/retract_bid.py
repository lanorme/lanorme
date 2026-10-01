from modules.bidding.application import bidding_module
from modules.bidding.domain.entities import Listing
from modules.bidding.domain.repositories import ListingRepository
from modules.bidding.domain.value_objects import Bidder
from seedwork.application.commands import Command
from seedwork.domain.value_objects import GenericUUID


class RetractBidCommand(Command):
    neutral_attribute_1: object
    neutral_attribute_2: object


@bidding_module.handler(RetractBidCommand)
def retract_bid(argument_0, argument_1):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    return value_4
