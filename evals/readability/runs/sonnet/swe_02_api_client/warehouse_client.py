"""A client for the internal Warehouse REST API.

Standard library only: HTTP requests go through ``urllib.request``.

Features:

* CRUD helpers for ``/v2/items`` (list, get, create, update, delete).
* Transparent cursor pagination via :meth:`WarehouseClient.iter_items`.
* Retry with exponential backoff and jitter on 429/5xx responses and on
  transport errors, honouring a ``Retry-After`` header when the server sends
  one. Gives up after a configurable number of attempts and raises
  :class:`WarehouseRetryExhaustedError`.
* An in-process TTL cache for GET responses, keyed by URL and query string.
* Bearer token auth with an optional refresh callback that is invoked once
  when a request comes back 401, after which the request is retried with the
  new token.
* Non-retryable HTTP errors are raised as :class:`WarehouseAPIError`, which
  carries the HTTP status and the (parsed, when possible) response body.

Example:

    client = WarehouseClient(
        "https://warehouse.example.com",
        token="abc123",
        refresh_token=lambda: fetch_fresh_token(),
    )
    for item in client.iter_items(warehouse_id="wh-1"):
        print(item)
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator, Mapping

__all__ = [
    "WarehouseClient",
    "WarehouseError",
    "WarehouseAPIError",
    "WarehouseAuthError",
    "WarehouseRetryExhaustedError",
]

JSONDict = dict[str, Any]

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


# --------------------------------------------------------------------------
# Public exceptions
# --------------------------------------------------------------------------


class WarehouseError(Exception):
    """Base class for every error this client raises."""


class WarehouseAPIError(WarehouseError):
    """A non-retryable HTTP error returned by the Warehouse API.

    Attributes:
        status: The HTTP status code.
        body: The response body, JSON-decoded when possible, otherwise the
            raw text (or ``None`` if the body was empty).
    """

    def __init__(self, status: int, body: Any, message: str | None = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message or f"Warehouse API error: HTTP {status}: {body!r}")


class WarehouseAuthError(WarehouseError):
    """Raised when a 401 could not be resolved by a token refresh.

    This happens when the server still returns 401 after the refresh
    callback has already been used once for this request, or when no
    refresh callback was configured at all.
    """

    def __init__(self, body: Any = None) -> None:
        self.status = 401
        self.body = body
        super().__init__("Warehouse API authentication failed (HTTP 401)")


class WarehouseRetryExhaustedError(WarehouseError):
    """Raised when a request still fails after all retry attempts.

    Attributes:
        attempts: How many attempts were made.
        last_error: The exception from the final attempt.
    """

    def __init__(self, attempts: int, last_error: BaseException | None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Warehouse request failed after {attempts} attempt(s); "
            f"last error: {last_error!r}"
        )


# --------------------------------------------------------------------------
# Internal control-flow exceptions (never escape the client)
# --------------------------------------------------------------------------


class _Unauthorized(Exception):
    def __init__(self, body: Any) -> None:
        self.body = body
        super().__init__("HTTP 401")


class _RetryableFailure(Exception):
    """A failure worth retrying: a 429/5xx response or a transport error."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class _RetryableHTTPError(_RetryableFailure):
    def __init__(self, status: int, body: Any, retry_after: float | None) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}", retry_after=retry_after)


class _RetryableTransportError(_RetryableFailure):
    pass


# --------------------------------------------------------------------------
# Response cache
# --------------------------------------------------------------------------


class _ResponseCache:
    """A tiny thread-safe TTL cache, keyed by full URL (path + query)."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, JSONDict]] = {}

    def get(self, key: str) -> JSONDict | None:
        if self._ttl <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return None
            return value

    def set(self, key: str, value: JSONDict) -> None:
        if self._ttl <= 0:
            return
        with self._lock:
            self._entries[key] = (time.monotonic() + self._ttl, value)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k.startswith(prefix)]:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header: either delta-seconds or an HTTP-date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class WarehouseClient:
    """A client for the internal Warehouse REST API.

    Args:
        base_url: e.g. ``"https://warehouse.example.com"`` (no trailing
            slash needed; ``/v2/...`` is appended to it).
        token: The initial bearer token.
        refresh_token: Optional zero-argument callable returning a fresh
            bearer token. Invoked at most once per request, when the server
            responds 401; the request is then retried with the new token.
        max_attempts: Maximum number of attempts for a request before giving
            up with :class:`WarehouseRetryExhaustedError`. Must be >= 1.
        backoff_base: Base delay (seconds) for exponential backoff.
        backoff_max: Cap (seconds) on the computed backoff delay, before
            jitter, and before any ``Retry-After`` override.
        cache_ttl: How long (seconds) a GET response stays cached. 0 (or
            negative) disables caching.
        timeout: Per-request socket timeout, in seconds.
        opener: Optional ``urllib.request.OpenerDirector`` to use instead of
            the default one (mainly for tests).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        refresh_token: Callable[[], str] | None = None,
        max_attempts: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 20.0,
        cache_ttl: float = 30.0,
        timeout: float = 10.0,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._refresh_token = refresh_token
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener()
        self._cache = _ResponseCache(ttl=cache_ttl)
        self._token_lock = threading.Lock()

    # -- Public item API ---------------------------------------------------

    def list_items(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        **filters: Any,
    ) -> JSONDict:
        """``GET /v2/items``. Returns one page: ``{"items": [...], "next_cursor": ...}``."""
        query: dict[str, Any] = dict(filters)
        if cursor is not None:
            query["cursor"] = cursor
        if limit is not None:
            query["limit"] = limit
        return self.request("GET", "/v2/items", query=query)

    def iter_items(self, **filters: Any) -> Iterator[JSONDict]:
        """Iterate every item across all pages, following ``next_cursor``.

        Accepts the same filter/limit keyword arguments as :meth:`list_items`.
        """
        cursor = filters.pop("cursor", None)
        while True:
            page = self.list_items(cursor=cursor, **filters)
            yield from page.get("items", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def get_item(self, item_id: str) -> JSONDict:
        """``GET /v2/items/{id}``."""
        return self.request("GET", self._item_path(item_id))

    def create_item(self, item: JSONDict) -> JSONDict:
        """``POST /v2/items``."""
        result = self.request("POST", "/v2/items", json_body=item, use_cache=False)
        self._cache.invalidate_prefix(self._items_prefix())
        return result

    def update_item(self, item_id: str, patch: JSONDict) -> JSONDict:
        """``PATCH /v2/items/{id}``."""
        result = self.request(
            "PATCH", self._item_path(item_id), json_body=patch, use_cache=False
        )
        self._cache.invalidate_prefix(self._items_prefix())
        return result

    def delete_item(self, item_id: str) -> JSONDict:
        """``DELETE /v2/items/{id}``."""
        result = self.request("DELETE", self._item_path(item_id), use_cache=False)
        self._cache.invalidate_prefix(self._items_prefix())
        return result

    # -- Generic request path ----------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        use_cache: bool | None = None,
    ) -> JSONDict:
        """Perform one logical request, applying caching, retries and auth.

        ``use_cache`` defaults to caching GET requests and nothing else.
        """
        method = method.upper()
        cacheable = use_cache if use_cache is not None else method == "GET"
        url = self._build_url(path, query)

        if cacheable:
            cached = self._cache.get(url)
            if cached is not None:
                return cached

        result = self._request_with_retries(method, url, json_body)

        if cacheable:
            self._cache.set(url, result)

        return result

    # -- Internals: URLs -----------------------------------------------------

    def _item_path(self, item_id: str) -> str:
        return f"/v2/items/{urllib.parse.quote(str(item_id), safe='')}"

    def _items_prefix(self) -> str:
        return f"{self._base_url}/v2/items"

    def _build_url(self, path: str, query: Mapping[str, Any] | None) -> str:
        url = f"{self._base_url}{path}"
        if not query:
            return url
        filtered = {k: v for k, v in query.items() if v is not None}
        if not filtered:
            return url
        encoded = urllib.parse.urlencode(sorted(filtered.items()), doseq=True)
        return f"{url}?{encoded}"

    # -- Internals: retry + auth loop ----------------------------------------

    def _request_with_retries(
        self, method: str, url: str, json_body: Any
    ) -> JSONDict:
        refreshed = False
        last_error: BaseException | None = None
        attempts = 0

        while True:
            try:
                return self._send_once(method, url, json_body)
            except _Unauthorized as exc:
                if refreshed or self._refresh_token is None:
                    raise WarehouseAuthError(exc.body) from exc
                with self._token_lock:
                    self._token = self._refresh_token()
                refreshed = True
                continue
            except _RetryableFailure as exc:
                last_error = exc

            attempts += 1
            if attempts >= self._max_attempts:
                raise WarehouseRetryExhaustedError(attempts, last_error) from last_error
            time.sleep(self._compute_delay(attempts, last_error.retry_after))

    def _compute_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        cap = min(self._backoff_max, self._backoff_base * (2 ** (attempt - 1)))
        return random.uniform(0, cap)

    # -- Internals: one HTTP call -------------------------------------------

    def _headers(self) -> dict[str, str]:
        with self._token_lock:
            token = self._token
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _send_once(self, method: str, url: str, json_body: Any) -> JSONDict:
        data = None
        headers = self._headers()
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                return self._decode_json_body(resp.read())
        except urllib.error.HTTPError as exc:
            body = self._decode_body(exc.read())
            if exc.code == 401:
                raise _Unauthorized(body) from exc
            if exc.code in _RETRYABLE_STATUSES:
                retry_after = _parse_retry_after(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                raise _RetryableHTTPError(exc.code, body, retry_after) from exc
            raise WarehouseAPIError(exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise _RetryableTransportError(str(exc.reason)) from exc
        except TimeoutError as exc:
            raise _RetryableTransportError(str(exc)) from exc

    @staticmethod
    def _decode_body(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="replace")

    @classmethod
    def _decode_json_body(cls, raw: bytes) -> JSONDict:
        body = cls._decode_body(raw)
        if body is None:
            return {}
        return body
