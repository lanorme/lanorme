from dataclasses import dataclass
from modules.bidding.application import bidding_module
from modules.bidding.domain.repositories import ListingRepository
from modules.bidding.domain.value_objects import Bid, Bidder, Money
from seedwork.application.commands import Command
from seedwork.domain.value_objects import GenericUUID


class PlaceBidCommand(Command):
    neutral_attribute_1: object
    neutral_attribute_2: object
    neutral_attribute_3: object
    neutral_attribute_4: object = 0


@bidding_module.handler(PlaceBidCommand)
def place_bid(argument_0, argument_1):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    return value_5
