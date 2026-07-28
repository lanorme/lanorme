"""A standard-library-only, in-memory double-entry ledger.

Typical usage::

    from datetime import date
    from ledger import AccountType, Ledger, PostingRequest, Side

    book = Ledger()
    cash = book.open_account("Cash", AccountType.ASSET)
    revenue = book.open_account("Consulting Revenue", AccountType.REVENUE)

    book.post(
        date=date(2026, 1, 5),
        memo="Invoice 1001",
        postings=[
            PostingRequest(cash.id, Side.DEBIT, 15_000),
            PostingRequest(revenue.id, Side.CREDIT, 15_000),
        ],
    )

    book.balance(cash.id)              # 15000
    book.trial_balance().is_balanced   # True

Amounts are always plain ``int`` minor units (e.g. cents) -- never floats.
Storage sits behind the narrow ``LedgerStore`` interface (see
``ledger.store``); ``InMemoryLedgerStore`` is the only implementation
provided, and a persistent one can replace it without touching the rest
of the domain.
"""

from .errors import (
    AccountClosedError,
    InvalidEntryError,
    LedgerError,
    PeriodClosedError,
    UnbalancedEntryError,
    UnknownAccountError,
)
from .models import Account, AccountType, JournalEntry, Posting, Side
from .service import Ledger, PostingRequest, TrialBalance, TrialBalanceLine
from .store import InMemoryLedgerStore, LedgerStore

__all__ = [
    "Account",
    "AccountClosedError",
    "AccountType",
    "InMemoryLedgerStore",
    "InvalidEntryError",
    "JournalEntry",
    "Ledger",
    "LedgerError",
    "LedgerStore",
    "PeriodClosedError",
    "Posting",
    "PostingRequest",
    "Side",
    "TrialBalance",
    "TrialBalanceLine",
    "UnbalancedEntryError",
    "UnknownAccountError",
]
