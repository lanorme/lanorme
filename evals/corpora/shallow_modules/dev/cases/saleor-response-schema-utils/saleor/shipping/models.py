from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Union, cast
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import OuterRef, Q, Subquery
from django_countries.fields import CountryField
from django_measurement.models import MeasurementField
from measurement.measures import Weight
from prices import Money
from ..channel.models import Channel
from ..core.db.fields import MoneyField, SanitizedJSONField
from ..core.editorjs import clean_editorjs
from ..core.models import ModelWithMetadata
from ..core.units import WeightUnits
from ..core.utils.translations import Translation
from ..core.weight import convert_weight, get_default_weight_unit, zero_weight
from ..permission.enums import ShippingPermissions
from ..tax.models import TaxClass
from . import PostalCodeRuleInclusionType, ShippingMethodType
from .postal_codes import filter_shipping_methods_by_postal_code_rules
from ..account.models import Address
from ..checkout.fetch import CheckoutLineInfo
from ..checkout.models import Checkout, CheckoutLine
from ..order.fetch import OrderLineInfo
from ..order.models import Order, OrderLine
from ..checkout.models import Checkout
from ..checkout.utils import calculate_checkout_weight
VALUE_0 = 0
