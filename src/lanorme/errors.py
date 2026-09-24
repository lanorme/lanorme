"""The exceptions LaNorme raises for a caller to handle.

A usage or configuration mistake (a path that does not exist, a selector that
names no rule, a TOML value of the wrong type, a malformed baseline) is not a
crash: the library raises :class:`UsageError` with the message a person needs,
and the CLI is the one place that turns it into ``ERROR: ...`` on stderr and
exit code 2. A library caller gets an exception it can catch instead of a
process that has already exited.
"""

from __future__ import annotations


class UsageError(Exception):
    """A mistake in how LaNorme was invoked or configured, phrased for the user."""
