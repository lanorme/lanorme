"""Importer stub: keeps only the imports that reach the labelled package."""
from polar.benefit.strategies.custom.properties import BenefitGrantCustomProperties
from polar.benefit.strategies.feature_flag.properties import (
    BenefitGrantFeatureFlagProperties,
)
from polar.benefit.strategies.license_keys.properties import (
    BenefitGrantLicenseKeysProperties,
)
from .strategies.custom.schemas import (
    BenefitCustom,
    BenefitCustomCreate,
    BenefitCustomUpdate,
)
from .strategies.feature_flag.schemas import (
    BenefitFeatureFlag,
    BenefitFeatureFlagCreate,
    BenefitFeatureFlagUpdate,
)
from .strategies.license_keys.schemas import (
    BenefitLicenseKeys,
    BenefitLicenseKeysCreate,
    BenefitLicenseKeysUpdate,
)

# Placeholders for names other case files import from this module.
BenefitCreate = None
BenefitID = None
