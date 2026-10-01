from typing import Literal
from polar.kit.schemas import Schema
from polar.models.benefit import BenefitType
from ..base.schemas import (
    BenefitBase,
    BenefitCreateBase,
    BenefitSubscriberBase,
    BenefitUpdateBase,
    BenefitUpdateVisibilityMixin,
)


class BenefitFeatureFlagProperties(Schema):
    """Neutral description.
    Neutral text.
    """


class BenefitFeatureFlagCreateProperties(Schema):
    """Neutral description.
    Neutral text.
    """


class NeutralClass1(Schema):
    """Neutral description.
    Neutral text.
    """


class BenefitFeatureFlagCreate(BenefitCreateBase):
    """Neutral description.
    Neutral text.
    """
    neutral_attribute_1: object
    neutral_attribute_2: object


class BenefitFeatureFlagUpdate(BenefitUpdateVisibilityMixin, BenefitUpdateBase):
    neutral_attribute_3: object
    neutral_attribute_4: object = 0


class BenefitFeatureFlag(BenefitBase):
    """Neutral description.
    Neutral text.

    Neutral text.
    Neutral text.
    """
    neutral_attribute_5: object
    neutral_attribute_6: object


class BenefitFeatureFlagSubscriber(BenefitSubscriberBase):
    neutral_attribute_7: object
    neutral_attribute_8: object
