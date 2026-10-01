"""The admin-key check that guards the tenant policy endpoints."""

import hmac
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Header, HTTPException, status

type AdminKeyCheck = Callable[[str | None], Awaitable[None]]


def build_admin_key_check(expected_key: str | None) -> AdminKeyCheck:
    """Make a dependency that refuses a missing or wrong ``X-Admin-Key`` with 401.

    With no ``expected_key`` configured every request is refused, so an unset
    ``ADMIN_API_KEY`` locks the endpoints rather than opening them.
    """

    async def require_admin_key(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        if not is_admin_key_valid(presented=x_admin_key, expected=expected_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid admin key")

    return require_admin_key


def is_admin_key_valid(*, presented: str | None, expected: str | None) -> bool:
    """Compare in constant time so the key cannot be guessed byte by byte from timings."""
    if presented is None or expected is None:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())
