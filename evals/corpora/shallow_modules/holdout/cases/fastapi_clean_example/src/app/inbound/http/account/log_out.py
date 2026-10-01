from inspect import getdoc
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, status
from fastapi.security import APIKeyCookie
from app.core.common.authorization.exceptions import AuthorizationError
from app.inbound.http.errors.callbacks import log_info
from app.inbound.http.errors.router import make_error_aware_router
from app.inbound.http.errors.rules import HTTP_503_SERVICE_UNAVAILABLE_RULE
from app.outbound.auth_ctx.exceptions import AuthenticationError
from app.outbound.auth_ctx.handlers.log_out import LogOut
from app.outbound.exceptions import StorageError


def make_log_out_router(*, keyword_0):
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
    return value_15
