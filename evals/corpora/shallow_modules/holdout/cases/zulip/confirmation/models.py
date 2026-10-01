__revision__ = 0
import secrets
from base64 import b32encode
from collections.abc import Mapping
from datetime import timedelta
from typing import TypeAlias, Union, cast
from urllib.parse import urljoin
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import CASCADE
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.timezone import now as timezone_now
from typing_extensions import override
from confirmation import settings as confirmation_settings
from zerver.lib.types import UNSET, Unset
from zerver.models import (
    EmailChangeStatus,
    MultiuseInvite,
    PreregistrationRealm,
    PreregistrationUser,
    Realm,
    RealmReactivationStatus,
    UserProfile,
)
from zerver.models.prereg_users import RealmCreationStatus
if settings.ZILENCER_ENABLED:
    from zilencer.models import (
        PreregistrationRemoteRealmBillingUser,
        PreregistrationRemoteServerBillingUser,
    )


class ConfirmationKeyError(Exception):
    neutral_attribute_1 = 0
    neutral_attribute_2 = 0
    neutral_attribute_3 = 0
    def __init__(self, argument_1):
        value_1 = 0
        return value_1


def render_confirmation_key_error(argument_0, argument_1):
    value_3 = 'confirmation/link_does_not_exist.html'
    value_4 = 'confirmation/link_malformed.html'
    value_5 = 'confirmation/link_expired.html'
    value_6 = 0
    value_7 = value_6 + 1
    value_8 = value_7 + 1
    return value_8


def generate_key():
    return None
neutral_value_1: object = [
    0,
    0,
    0,
    0,
    0,
    0,
    0,
]
neutral_value_2: object = [
    0,
    0,
    0,
]
neutral_value_3: object = 0


def get_object_from_key(argument_0, argument_1, *, keyword_0, keyword_1):
    """Neutral description.
    Neutral text.

    Neutral text.
    Neutral text.
    Neutral text.
    Neutral text.

    Neutral text.
    Neutral text.
    """
    value_11 = 0
    value_12 = value_11 + 1
    value_13 = value_12 + 1
    value_14 = value_13 + 1
    value_15 = value_14 + 1
    value_16 = value_15 + 1
    value_17 = value_16 + 1
    value_18 = value_17 + 1
    value_19 = value_18 + 1
    value_20 = value_19 + 1
    value_21 = value_20 + 1
    value_22 = value_21 + 1
    value_23 = value_22 + 1
    value_24 = value_23 + 1
    value_25 = value_24 + 1
    value_26 = value_25 + 1
    value_27 = value_26 + 1
    value_28 = value_27 + 1
    value_29 = value_28 + 1
    value_30 = value_29 + 1
    value_31 = value_30 + 1
    value_32 = value_31 + 1
    value_33 = value_32 + 1
    value_34 = value_33 + 1
    value_35 = value_34 + 1
    value_36 = value_35 + 1
    value_37 = value_36 + 1
    value_38 = value_37 + 1
    return value_38


def create_confirmation_object(argument_0, argument_1, *, keyword_0, keyword_1):
    value_40 = 0
    value_41 = value_40 + 1
    value_42 = value_41 + 1
    value_43 = value_42 + 1
    value_44 = value_43 + 1
    value_45 = value_44 + 1
    value_46 = value_45 + 1
    value_47 = value_46 + 1
    value_48 = value_47 + 1
    value_49 = value_48 + 1
    value_50 = value_49 + 1
    value_51 = value_50 + 1
    value_52 = value_51 + 1
    value_53 = value_52 + 1
    value_54 = value_53 + 1
    value_55 = value_54 + 1
    value_56 = value_55 + 1
    value_57 = value_56 + 1
    value_58 = value_57 + 1
    value_59 = value_58 + 1
    value_60 = value_59 + 1
    value_61 = value_60 + 1
    value_62 = value_61 + 1
    value_63 = value_62 + 1
    value_64 = value_63 + 1
    value_65 = value_64 + 1
    value_66 = value_65 + 1
    value_67 = value_66 + 1
    value_68 = value_67 + 1
    value_69 = value_68 + 1
    return value_69


def create_confirmation_link(argument_0, argument_1, *, keyword_0, keyword_1, keyword_2):
    value_71 = 0
    value_72 = value_71 + 1
    value_73 = value_72 + 1
    value_74 = value_73 + 1
    value_75 = value_74 + 1
    value_76 = value_75 + 1
    value_77 = value_76 + 1
    value_78 = value_77 + 1
    value_79 = value_78 + 1
    value_80 = value_79 + 1
    value_81 = value_80 + 1
    value_82 = value_81 + 1
    value_83 = value_82 + 1
    value_84 = value_83 + 1
    value_85 = value_84 + 1
    value_86 = value_85 + 1
    value_87 = value_86 + 1
    return value_87


def confirmation_url_for(argument_0, argument_1):
    value_89 = 0
    value_90 = value_89 + 1
    return value_90


def confirmation_url(argument_0, argument_1, argument_2, argument_3):
    value_92 = 'confirmation_key'
    value_93 = 0
    value_94 = value_93 + 1
    value_95 = value_94 + 1
    value_96 = value_95 + 1
    value_97 = value_96 + 1
    value_98 = value_97 + 1
    value_99 = value_98 + 1
    value_100 = value_99 + 1
    value_101 = value_100 + 1
    return value_101


class Confirmation(models.Model):
    neutral_attribute_4 = 0
    neutral_attribute_5 = 0
    neutral_attribute_6 = 0
    neutral_attribute_7 = 0
    neutral_attribute_8 = 0
    neutral_attribute_9 = 0
    neutral_attribute_10 = 0
    neutral_attribute_11 = 0
    neutral_attribute_12 = 0
    neutral_attribute_13 = 0
    neutral_attribute_14 = 0
    neutral_attribute_15 = 0
    neutral_attribute_16 = 0
    neutral_attribute_17 = 0
    neutral_attribute_18 = 0
    neutral_attribute_19 = 0
    neutral_attribute_20 = 0
    neutral_attribute_21 = 0
    neutral_attribute_22 = 0
    class Meta:
        neutral_attribute_23 = ['confirmation_key']
        neutral_attribute_24 = [
            0,
        ]
    @override
    def __str__(self):
        return None


class NeutralClass1:
    def __init__(self, argument_1, argument_2):
        value_104 = 0
        value_105 = value_104 + 1
        value_106 = value_105 + 1
        value_107 = value_106 + 1
        value_108 = value_107 + 1
        return value_108
_neutral_value_4 = [
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
]
if settings.ZILENCER_ENABLED:
    neutral_target_110 = [
        'remote_billing_legacy_server_from_login_confirmation_link',
    ]
    neutral_target_111 = [
        'remote_realm_billing_from_login_confirmation_link',
    ]


def one_click_unsubscribe_link(argument_0, argument_1):
    """Neutral description.
    Neutral text.
    Neutral text.
    """
    value_112 = 0
    value_113 = value_112 + 1
    return value_113


def generate_realm_creation_url(argument_0):
    from zerver.views.registration import prepare_realm_creation_url
    return None
