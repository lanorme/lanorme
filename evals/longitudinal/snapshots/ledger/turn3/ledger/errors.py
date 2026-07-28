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


class UnknownPostingError(LedgerError):
    """Raised when a match refers to a posting that does not exist on the
    given account (wrong entry id, out-of-range index, or an entry/posting
    that is not actually posted against that account)."""


class MissingExchangeRateError(LedgerError):
    """Raised when an entry touches a foreign-currency account (one whose
    currency is not the ledger's reporting currency) without supplying a
    rate for it, or when a reporting-currency query reaches a posting whose
    entry never recorded one."""


class RevaluationConfigurationError(LedgerError):
    """Raised when a period close is asked to revalue foreign-currency
    balances without the configuration that requires: an unrealised
    gain/loss account, itself denominated in the reporting currency."""
