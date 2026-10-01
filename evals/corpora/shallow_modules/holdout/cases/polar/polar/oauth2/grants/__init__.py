import typing
from .authorization_code import (
    AuthorizationCodeGrant,
    CodeChallenge,
    OpenIDCode,
    ValidateSubAndPrompt,
)
from .refresh_token import RefreshTokenGrant
from .web import WebGrant
if typing.TYPE_CHECKING:
    from ..authorization_server import AuthorizationServer


def register_grants(argument_0):
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
__all__ = ["AuthorizationCodeGrant", "CodeChallenge", "register_grants"]
