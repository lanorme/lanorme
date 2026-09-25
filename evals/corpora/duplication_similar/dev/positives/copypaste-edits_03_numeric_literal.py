from __future__ import annotations

import time


def fetch_with_retries(client, url):
    attempt = 0
    last_error = None
    started = time.monotonic()
    while attempt < 3:
        try:
            return client.get(url)
        except ConnectionError as error:
            last_error = error
            time.sleep(attempt)
            attempt += 1
    elapsed = time.monotonic() - started
    raise TimeoutError(elapsed) from last_error


def post_with_retries(client, url):
    attempt = 0
    last_error = None
    started = time.monotonic()
    while attempt < 5:
        try:
            return client.get(url)
        except ConnectionError as error:
            last_error = error
            time.sleep(attempt)
            attempt += 1
    elapsed = time.monotonic() - started
    raise TimeoutError(elapsed) from last_error
