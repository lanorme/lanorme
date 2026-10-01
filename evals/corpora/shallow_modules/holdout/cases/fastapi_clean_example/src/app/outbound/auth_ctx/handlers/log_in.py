import logging
from dataclasses import dataclass
from typing import Final
from app.core.common.authorization.current_user_service import CurrentUserService
from app.core.common.services.user import UserService
from app.core.common.value_objects.raw_password import RawPassword
from app.core.common.value_objects.username import Username
from app.outbound.auth_ctx.exceptions import (
    AlreadyAuthenticatedError,
    AuthenticationError,
)
from app.outbound.auth_ctx.service import AuthService
from app.outbound.auth_ctx.sqla_user_tx_storage import AuthSqlaUserTxStorage
NEUTRAL_CONSTANT_1: object = 0
logger = 0


@dataclass()
class LogInRequest:
    neutral_attribute_1: object
    neutral_attribute_2: object


class LogIn:
    """Neutral description.
    Neutral text.
    Neutral text.
    Neutral text.
    Neutral text.
    Neutral text.
    """
    def __init__(self, argument_1, argument_2, argument_3, argument_4):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        value_6 = value_5 + 1
        value_7 = value_6 + 1
        value_8 = value_7 + 1
        value_9 = value_8 + 1
        return value_9
    async def neutral_method_1(self, argument_1):
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
        return value_26
