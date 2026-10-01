import logging
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, TypedDict
from uuid import UUID
import graphene
from django.contrib.sites.models import Site
from django.db import transaction
from django.db.models import F
from ..account.models import User
from ..app.models import App
from ..core.exceptions import AllocationError, InsufficientStock, InsufficientStockData
from ..core.tracing import traced_atomic_transaction
from ..core.transactions import transaction_with_commit_on_errors
from ..core.utils.events import call_event
from ..giftcard import GiftCardLineData
from ..order.lock_objects import order_lines_qs_select_for_update
from ..payment import ChargeStatus, CustomPaymentChoices, PaymentError, TransactionAction, TransactionKind
from ..payment.interface import RefundData
from ..payment.models import Payment, Transaction, TransactionItem
from ..plugins.manager import PluginsManager
from ..warehouse.management import deallocate_stock, deallocate_stock_for_orders, decrease_stock
from ..warehouse.models import Stock
from ..webhook.event_types import WebhookEventAsyncType
from ..webhook.utils import get_webhooks_for_multiple_events
from . import FulfillmentLineData, FulfillmentStatus, OrderChargeStatus, OrderOrigin, OrderStatus, events
from .events import draft_order_created_from_replace_event, fulfillment_refunded_event, fulfillment_replaced_event, order_replacement_created, order_returned_event
from .fetch import OrderLineInfo
from .models import Fulfillment, FulfillmentLine, Order, OrderLine
from .notifications import send_fulfillment_confirmation_to_customer, send_order_canceled_confirmation, send_order_confirmed, send_order_refunded_confirmation, send_payment_confirmation
from .utils import clean_order_line_quantities, restock_fulfillment_lines, update_order_authorize_data, update_order_charge_data, update_order_status, updates_amounts_for_order
from ..app.models import App
from ..page.models import Page
from ..site.models import SiteSettings
from ..warehouse.models import Warehouse
from ..webhook.models import Webhook
from .fetch import OrderInfo
from ..giftcard.gateway import charge_gift_card_transactions
from ..giftcard.utils import fulfill_non_shippable_gift_cards
from ..giftcard.utils import gift_cards_create
from ..payment.utils import create_transaction_for_order
from ..payment.utils import create_payment
from ..payment.gateway import refund
VALUE_0 = 0
