from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, cast
from uuid import UUID
import graphene
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone
from prices import Money
from ..account.models import User
from ..core.db.connection import allow_writer
from ..core.exceptions import NonExistingCheckout, NonExistingCheckoutLines
from ..core.taxes import zero_taxed_money
from ..core.utils.metadata_manager import MetadataItemCollection, MetadataType, store_on_instance
from ..core.utils.promo_code import InvalidPromoCode, promo_code_is_gift_card, promo_code_is_voucher
from ..core.utils.translations import get_translation
from ..core.weight import zero_weight
from ..discount import DiscountType, VoucherType
from ..discount.interface import fetch_voucher_info
from ..discount.models import CheckoutDiscount, NotApplicable, Voucher, VoucherCode
from ..discount.utils.checkout import create_checkout_discount_objects_for_order_promotions, create_checkout_line_discount_objects_for_catalogue_promotions
from ..discount.utils.promotion import delete_gift_line
from ..discount.utils.shared import discount_info_for_logs
from ..discount.utils.voucher import get_discounted_lines, get_products_voucher_discount, get_voucher_code_instance, validate_voucher_for_checkout
from ..giftcard.utils import add_gift_card_code_to_checkout, remove_gift_card_code_from_checkout_or_error
from ..payment.models import Payment
from ..plugins.manager import PluginsManager
from ..product import models as product_models
from ..warehouse.reservations import reserve_stocks_and_preorders
from . import AddressType, base_calculations, calculations
from .delivery_context import is_shipping_required
from .error_codes import CheckoutErrorCode
from .lock_objects import checkout_lines_qs_select_for_update, checkout_qs_select_for_update
from .models import Checkout, CheckoutLine, CheckoutMetadata
from measurement.measures import Weight
from ..account.models import Address
from ..core.pricing.interface import LineInfo
from ..order.models import OrderLine
from .fetch import CheckoutInfo, CheckoutLineInfo
VALUE_0 = 0
