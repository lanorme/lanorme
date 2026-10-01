import json
import logging
from decimal import Decimal
from typing import Any, Optional, cast, get_args, overload
from uuid import UUID
import graphene
from babel.numbers import get_currency_precision
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import ValidationError
from ..account.models import User
from ..app.models import App
from ..channel import TransactionFlowStrategy
from ..checkout import calculations
from ..checkout.actions import transaction_amounts_for_checkout_updated, transaction_amounts_for_checkout_updated_without_price_recalculation, update_last_transaction_modified_at_for_checkout
from ..checkout.fetch import fetch_checkout_info, fetch_checkout_lines
from ..checkout.models import Checkout
from ..checkout.payment_utils import update_refundable_for_checkout
from ..core.db.connection import allow_writer
from ..core.prices import quantize_price
from ..core.tracing import traced_atomic_transaction
from ..core.utils.text import safe_truncate
from ..giftcard.const import GIFT_CARD_PAYMENT_GATEWAY_ID
from ..graphql.core.utils import str_to_enum
from ..order import OrderStatus
from ..order.actions import order_transaction_updated
from ..order.fetch import fetch_order_info
from ..order.models import Order, OrderGrantedRefund
from ..order.search import update_order_search_vector
from ..order.utils import calculate_order_granted_refund_status, refresh_order_status, update_order_authorize_data, updates_amounts_for_order
from ..plugins.manager import PluginsManager, get_plugins_manager
from ..webhook.response_schemas import transaction as transaction_schemas
from ..webhook.response_schemas.utils.helpers import parse_validation_error
from ..webhook.transport.list_stored_payment_methods import invalidate_cache_for_stored_payment_methods
from . import ChargeStatus, GatewayError, PaymentError, PaymentMethodType, StorePaymentMethod, TransactionAction, TransactionEventType, TransactionItemIdempotencyUniqueError, TransactionKind
from .error_codes import PaymentErrorCode
from .interface import AddressData, GatewayResponse, PaymentData, PaymentGatewayData, PaymentLineData, PaymentLinesData, PaymentMethodDetails, PaymentMethodInfo, RefundData, StorePaymentMethodEnum, TransactionData, TransactionProcessActionData, TransactionRequestEventResponse, TransactionRequestResponse, TransactionSessionData, TransactionSessionResponse
from .lock_objects import get_checkout_and_transaction_item_locked_for_update, get_order_and_transaction_item_locked_for_update
from .models import Payment, Transaction, TransactionEvent, TransactionItem
from .transaction_item_calculations import recalculate_transaction_amounts
from .gateway import payment_refund_or_void
from ..checkout.utils import get_checkout_metadata
from ..giftcard.gateway import transaction_initialize_session_with_gift_card_payment_method
from ..order.actions import order_transaction_updated
VALUE_0 = 0
