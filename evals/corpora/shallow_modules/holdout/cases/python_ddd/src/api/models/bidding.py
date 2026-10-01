from datetime import datetime
from pydantic import BaseModel
from seedwork.domain.value_objects import GenericUUID


class NeutralClass1(BaseModel):
    neutral_attribute_1: object
    neutral_attribute_2: object
    neutral_attribute_3: object
    neutral_attribute_4: object
    class Config:
        neutral_attribute_5 = 0


class BiddingResponse(BaseModel):
    neutral_attribute_6: object
    neutral_attribute_7: object = 0
    neutral_attribute_8: object
    neutral_attribute_9: object
    class Config:
        neutral_attribute_10 = 0


class PlaceBidRequest(BaseModel):
    neutral_attribute_11: object
    neutral_attribute_12: object
    class Config:
        neutral_attribute_13 = 0
