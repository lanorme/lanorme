"""Exceptions raised by the ledger domain."""


class LedgerError(Exception):
    """Base class for all ledger domain errors."""


class UnknownAccountError(LedgerError):
    """Raised when a posting references an account that does not exist."""


class AccountClosedError(LedgerError):
    """Raised when a posting targets a closed account."""


class UnbalancedEntryError(LedgerError):
    """Raised when an entry's debits and credits do not balance exactly."""


class InvalidEntryError(LedgerError):
    """Raised when an entry is structurally invalid (fewer than two postings)."""


class PeriodClosedError(LedgerError):
    """Raised when an entry is dated within a closed accounting period."""
