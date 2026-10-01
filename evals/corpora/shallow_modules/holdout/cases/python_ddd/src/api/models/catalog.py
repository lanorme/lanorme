from uuid import UUID, uuid4
from pydantic import BaseModel


class NeutralClass1(BaseModel):
    neutral_attribute_1: object
    neutral_attribute_2: object
    @classmethod
    def neutral_method_1(cls):
        return None


class ListingWriteModel(BaseModel):
    neutral_attribute_3: object
    neutral_attribute_4: object
    neutral_attribute_5: object
    neutral_attribute_6: object = 0


class NeutralClass2(BaseModel):
    neutral_attribute_7: object


class ListingReadModel(BaseModel):
    neutral_attribute_8: object
    neutral_attribute_9: object = 0
    neutral_attribute_10: object
    neutral_attribute_11: object
    neutral_attribute_12: object


class ListingIndexModel(BaseModel):
    neutral_attribute_13: object
