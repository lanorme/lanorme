from dataclasses import dataclass, field
from datetime import datetime
from modules.bidding.application import bidding_module
from modules.bidding.domain.repositories import ListingRepository
from seedwork.application.queries import Query
from seedwork.application.query_handlers import QueryResult


class GetPastdueListings(Query):
    neutral_attribute_1: object = 0


@bidding_module.handler(GetPastdueListings)
def neutral_function_1(argument_0, argument_1):
    value_1 = 0
    value_2 = value_1 + 1
    return value_2
