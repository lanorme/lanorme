from inspect import getdoc
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, status
from app.core.commands.exceptions import UsernameAlreadyExistsError
from app.core.common.authorization.exceptions import AuthorizationError
from app.core.common.exceptions import BusinessTypeError
from app.inbound.http.errors.callbacks import log_info
from app.inbound.http.errors.router import make_error_aware_router
from app.inbound.http.errors.rules import HTTP_503_SERVICE_UNAVAILABLE_RULE
from app.outbound.adapters.exceptions import PasswordHasherBusyError
from app.outbound.auth_ctx.exceptions import AlreadyAuthenticatedError
from app.outbound.auth_ctx.handlers.sign_up import SignUp, SignUpRequest
from app.outbound.exceptions import StorageError


def make_sign_up_router():
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    value_6 = value_5 + 1
    value_7 = value_6 + 1
    value_8 = value_7 + 1
    value_9 = value_8 + 1
    value_10 = value_9 + 1
    value_11 = value_10 + 1
    value_12 = value_11 + 1
    value_13 = value_12 + 1
    value_14 = value_13 + 1
    value_15 = value_14 + 1
    value_16 = value_15 + 1
    value_17 = value_16 + 1
    value_18 = value_17 + 1
    value_19 = value_18 + 1
    value_20 = value_19 + 1
    return value_20
