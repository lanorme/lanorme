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

Bank reconciliation (``ledger.reconciler.Reconciler``) matches an imported
bank statement against one account's postings, in three passes -- exact
amount and date, amount within a date window, then a fuzzy match on
description -- and reports matches, unmatched lines on both sides, and the
closing difference. A human's accept or reject of a proposed match is
persisted behind ``ReconciliationStore`` so a rerun does not re-propose a
rejected pair::

    from ledger import Reconciler, StatementLine

    reconciler = Reconciler(book)
    report = reconciler.reconcile(
        cash.id,
        statement_lines=[StatementLine(date(2026, 1, 6), 15_000, "INV1001", "S-1")],
    )
    for match in report.matches:
        reconciler.accept(cash.id, match)
"""

from .errors import (
    AccountClosedError,
    InvalidEntryError,
    LedgerError,
    PeriodClosedError,
    UnbalancedEntryError,
    UnknownAccountError,
    UnknownPostingError,
)
from .models import Account, AccountType, JournalEntry, Posting, Side
from .reconciler import Reconciler
from .reconciliation_models import (
    Match,
    MatchDecision,
    MatchRule,
    PostingRef,
    PostingSummary,
    ReconciliationReport,
    StatementLine,
)
from .reconciliation_store import InMemoryReconciliationStore, ReconciliationStore
from .service import Ledger, PostingRequest, TrialBalance, TrialBalanceLine
from .store import InMemoryLedgerStore, LedgerStore

__all__ = [
    "Account",
    "AccountClosedError",
    "AccountType",
    "InMemoryLedgerStore",
    "InMemoryReconciliationStore",
    "InvalidEntryError",
    "JournalEntry",
    "Ledger",
    "LedgerError",
    "LedgerStore",
    "Match",
    "MatchDecision",
    "MatchRule",
    "PeriodClosedError",
    "Posting",
    "PostingRef",
    "PostingRequest",
    "PostingSummary",
    "Reconciler",
    "ReconciliationReport",
    "ReconciliationStore",
    "Side",
    "StatementLine",
    "TrialBalance",
    "TrialBalanceLine",
    "UnbalancedEntryError",
    "UnknownAccountError",
    "UnknownPostingError",
]
