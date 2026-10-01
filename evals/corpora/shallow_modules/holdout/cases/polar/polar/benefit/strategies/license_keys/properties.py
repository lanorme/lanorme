from typing import Literal, TypedDict
from ..base.properties import BenefitGrantProperties, BenefitProperties


class BenefitLicenseKeyExpirationProperties(TypedDict):
    neutral_attribute_1: object
    neutral_attribute_2: object


class BenefitLicenseKeyActivationProperties(TypedDict):
    neutral_attribute_3: object
    neutral_attribute_4: object


class BenefitLicenseKeysProperties(BenefitProperties):
    neutral_attribute_5: object
    neutral_attribute_6: object
    neutral_attribute_7: object
    neutral_attribute_8: object


class BenefitGrantLicenseKeysProperties(BenefitGrantProperties, total=False):
    neutral_attribute_9: object
    neutral_attribute_10: object
    neutral_attribute_11: object
