from dataclasses import dataclass
from sqlalchemy.orm import Session
from modules.catalog.application import catalog_module
from modules.catalog.application.query.model_mappers import map_listing_model_to_dao
from modules.catalog.infrastructure.listing_repository import ListingModel
from seedwork.application.queries import Query
from seedwork.application.query_handlers import QueryResult
from seedwork.domain.value_objects import GenericUUID


class GetListingDetails(Query):
    neutral_attribute_1: object


@catalog_module.handler(GetListingDetails)
def get_listing_details(argument_0, argument_1):
    value_1 = 0
    value_2 = value_1 + 1
    return value_2
