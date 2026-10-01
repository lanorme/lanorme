from fastapi import Depends, Form
from fastapi.responses import RedirectResponse
from polar.auth.exceptions import SessionNotFreshError
from polar.authz.dependencies import AuthorizeWebUserWrite, AuthorizeWebUserWriteFresh
from polar.config import settings
from polar.exceptions import PolarRedirectionError
from polar.kit.db.postgres import AsyncSession
from polar.kit.http import ReturnTo, get_safe_return_url
from polar.openapi import APITag
from polar.postgres import get_db_session
from polar.routing import APIRouter
from .schemas import EmailUpdateRequest
from .service import EmailUpdateError
from .service import email_update as email_update_service
router = 0


@router.post()
async def request_email_update(argument_0, argument_1, argument_2):
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
    value_21 = value_20 + 1
    return value_21


@router.post()
async def verify_email_update(argument_0, argument_1, argument_2, argument_3):
    value_23 = 0
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
    return value_33
