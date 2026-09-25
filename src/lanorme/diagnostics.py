"""Where the CLI's diagnostics go: the ``lanorme`` logger, formatted for stderr.

Findings are the product and go to stdout through the reporters. Everything
that is *about* a run (a usage error, a note that nothing was checked, a
selected check that is not enabled) is logged under the ``lanorme`` logger,
so a library caller can capture or silence it and the output stream stays
pure. The CLI attaches one handler that spells messages the way it always
has: ``ERROR: ...`` and ``Note: ...``.
"""

from __future__ import annotations

import logging
import sys


class StderrFormatter(logging.Formatter):
    """Diagnostics as the CLI has always spelled them: ``ERROR: ...`` and ``Note: ...``."""

    _PREFIXES = {logging.ERROR: "ERROR", logging.WARNING: "Note", logging.INFO: "Note"}

    def format(self, record: logging.LogRecord) -> str:
        prefix = self._PREFIXES.get(record.levelno, record.levelname)
        return f"{prefix}: {record.getMessage()}"


def configure_diagnostics() -> None:
    """Send the ``lanorme`` logger's diagnostics to stderr, once per process.

    Findings go to stdout through the reporters; everything that is *about*
    the run (a usage error, a note that nothing was checked) is logged, so a
    library caller can capture or silence it and the output stream stays pure.
    """
    log = logging.getLogger("lanorme")
    if any(isinstance(handler, StderrHandler) for handler in log.handlers):
        return
    handler = StderrHandler()
    handler.setFormatter(StderrFormatter())
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


class StderrHandler(logging.StreamHandler):
    """A handler that writes to whatever ``sys.stderr`` is at emit time.

    ``StreamHandler`` captures the stream when built; a test harness (or a
    caller) that swaps ``sys.stderr`` later would otherwise never see the
    message.
    """

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self) -> object:
        return sys.stderr

    @stream.setter
    def stream(self, _value: object) -> None:
        return None
