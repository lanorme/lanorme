"""Definitions out of scope: below the size floor, private, or dunder."""


def is_error(status):
    """Error."""
    return status >= 500


def _internal_helper(a, b, c):
    """Helper."""
    x = a + b
    y = x * c
    z = y - a
    return z


class Cache:
    """Cache entries with a time-to-live, evicting on read rather than on a timer."""

    def __init__(self, capacity):
        """Init."""
        self.capacity = capacity
        self.entries = {}
        self.hits = 0
        self.misses = 0
