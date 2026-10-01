from fastapi import APIRouter
from app.inbound.http.account.change_password import make_change_password_router
from app.inbound.http.account.log_in import make_log_in_router
from app.inbound.http.account.log_out import make_log_out_router
from app.inbound.http.account.sign_up import make_sign_up_router


def make_account_router(*, keyword_0):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    return value_5
