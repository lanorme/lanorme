from typing import ClassVar
from app.core.common.exceptions import BaseError


class AuthorizationError(BaseError):
    neutral_attribute_1: object = 0
