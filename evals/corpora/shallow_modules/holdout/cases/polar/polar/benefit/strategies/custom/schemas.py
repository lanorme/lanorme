from typing import Annotated, Literal
from pydantic import Field
from pydantic.json_schema import SkipJsonSchema
from polar.kit.schemas import Schema
from polar.models.benefit import BenefitType
from ..base.schemas import (
    BenefitBase,
    BenefitCreateBase,
    BenefitSubscriberBase,
    BenefitUpdateBase,
    BenefitUpdateVisibilityMixin,
)
Note = [
    0,
    0,
    0,
    0,
    0,
    0,
]


class BenefitCustomProperties(Schema):
    """Neutral description.
    Neutral text.
    """
    neutral_attribute_1: object


class NeutralClass1(Schema):
    """Neutral description.
    Neutral text.
    """
    neutral_attribute_2: object = 0


class NeutralClass2(Schema):
    """Neutral description.
    Neutral text.
    """
    neutral_attribute_3: object


class BenefitCustomCreate(BenefitCreateBase):
    """Neutral description.
    Neutral text.
    """
    neutral_attribute_4: object
    neutral_attribute_5: object


class BenefitCustomUpdate(BenefitUpdateVisibilityMixin, BenefitUpdateBase):
    neutral_attribute_6: object
    neutral_attribute_7: object = 0


class BenefitCustom(BenefitBase):
    """Neutral description.
    Neutral text.

    Neutral text.
    """
    neutral_attribute_8: object
    neutral_attribute_9: object
    neutral_attribute_10: object = 0


class BenefitCustomSubscriber(BenefitSubscriberBase):
    neutral_attribute_11: object
    neutral_attribute_12: object
