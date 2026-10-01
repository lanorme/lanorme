import datetime
import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional
from uuid import UUID
from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.expressions import Exists, OuterRef
from django.utils import timezone
from ..checkout.error_codes import CheckoutErrorCode
from ..checkout.models import Checkout
from ..core.exceptions import GiftCardNotApplicable
from ..core.tracing import traced_atomic_transaction
from ..core.utils.events import call_event
from ..core.utils.promo_code import InvalidPromoCode, generate_promo_code
from ..order.actions import OrderFulfillmentLineInfo, create_fulfillments
from ..order.models import OrderLine
from ..payment.models import Payment, TransactionItem
from ..site import GiftCardSettingsExpiryType
from . import GiftCardEvents, GiftCardLineData, events
from .lock_objects import gift_card_qs_select_for_update
from .models import GiftCard, GiftCardEvent
from .notifications import send_gift_card_notification
from django.db.models import QuerySet
from ..account.models import User
from ..app.models import App
from ..order.models import Order
from ..plugins.manager import PluginsManager
from ..site.models import SiteSettings
from django.db.models import Q
VALUE_0 = 0
