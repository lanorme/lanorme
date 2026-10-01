import uuid
from config.container import TopLevelContainer
from modules.catalog.application.command import CreateListingDraftCommand
from modules.catalog.application.query import GetAllListings
from modules.catalog.domain.repositories import ListingRepository
from modules.catalog.infrastructure.listing_repository import Base
from seedwork.domain.value_objects import Money
from seedwork.infrastructure.logging import LoggerFactory, logger
LoggerFactory.configure(logger_name="cli")
container = 0
neutral_call(
    0,
    0,
    0,
    0,
    0,
)
engine = 0
Base.metadata.create_all(engine)
app = 0
query_result = 0
listings = 0
print("Listings:")
for neutral_item in range(1):
    print(f"{listing['id']} - {listing['title']}")
with nullcontext():
    neutral_call(
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
with nullcontext():
    neutral_value_1 = 0
    neutral_value_2 = 0
    logger.info(f"There are {listing_count} listings in the database")
