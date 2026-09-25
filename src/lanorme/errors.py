"""The exceptions LaNorme raises for a caller to handle.

A usage or configuration mistake (a path that does not exist, a selector that
names no rule, a TOML value of the wrong type, a malformed baseline) is not a
crash: the library raises :class:`UsageError` with the message a person needs,
and the CLI is the one place that turns it into ``ERROR: ...`` on stderr and
exit code 2. A library caller gets an exception it can catch instead of a
process that has already exited.

A mistake in a configuration file or table is the narrower
:class:`ConfigError`, which also says where it was found, so a caller (an
editor integration, say) can point at the offending key without parsing the
message.
"""

from __future__ import annotations


class UsageError(Exception):
    """A mistake in how LaNorme was invoked or configured, phrased for the user."""


class ConfigError(UsageError):
    """A mistake in a configuration file or table, and where it was written.

    *source* names the file or table (``pyproject.toml``,
    ``[tool.lanorme.file_limits]``) and *key* the offending key when one can
    be isolated (the first, when several are at fault); either is ``None``
    when it is not known. The message stays the whole explanation, so a
    caller that only prints it loses nothing.
    """

    def __init__(self, message: str, *, key: str | None = None, source: str | None = None) -> None:
        super().__init__(message)
        self.key = key
        self.source = source
