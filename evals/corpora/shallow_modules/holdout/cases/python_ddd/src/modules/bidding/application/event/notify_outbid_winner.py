from modules.bidding.application import bidding_module
from modules.bidding.domain.events import BidWasPlaced
from seedwork.infrastructure.logging import logger


@bidding_module.handler(BidWasPlaced)
def notify_outbid_winner(argument_0):
    return None
