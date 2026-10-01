import datetime
from decimal import Decimal
from operator import attrgetter
from typing import TYPE_CHECKING, Optional
from uuid import uuid4
from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.encoding import smart_str
from django_countries.fields import Country, CountryField
from prices import Money
from ..channel.models import Channel
from ..core.db.fields import MoneyField, TaxedMoneyField
from ..core.models import ModelWithMetadata
from ..core.taxes import TAX_ERROR_FIELD_LENGTH, zero_money
from ..core.utils.json_serializer import CustomJsonEncoder
from ..giftcard.models import GiftCard
from ..permission.enums import CheckoutPermissions
from ..shipping.models import ShippingMethod
from . import CheckoutAuthorizeStatus, CheckoutChargeStatus
from ..payment.models import Payment
from ..product.models import ProductVariant
from ..core.db.connection import allow_writer
VALUE_0 = 0
