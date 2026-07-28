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

Every account carries a currency (``AccountType`` needs no currency of its
own; it is set per account, defaulting to the ledger's reporting currency).
An entry may span currencies as long as it balances in the reporting
currency at the rates it supplies::

    book = Ledger(reporting_currency="USD")
    cash_usd = book.open_account("Cash (USD)", AccountType.ASSET)
    cash_eur = book.open_account("Cash (EUR)", AccountType.ASSET, currency="EUR")
    fx_expense = book.open_account("FX Fees", AccountType.EXPENSE)

    book.post(
        date=date(2026, 1, 5),
        memo="Convert EUR to USD",
        postings=[
            PostingRequest(cash_usd.id, Side.DEBIT, 10_800),
            PostingRequest(cash_eur.id, Side.CREDIT, 10_000),
        ],
        rates={"EUR": Decimal("1.08")},
    )

    book.balance(cash_eur.id)                                  # 10000, in EUR
    book.balance(cash_eur.id, currency=CurrencyMode.REPORTING)  # 10800, in USD

``Ledger.close_period`` can revalue foreign-currency balances at a closing
rate, posting the unrealised gain or loss to a configured account, before
freezing the period to new postings.
"""

from .currency import Currency, CurrencyMode, validate_currency
from .errors import (
    AccountClosedError,
    InvalidEntryError,
    LedgerError,
    MissingExchangeRateError,
    PeriodClosedError,
    RevaluationConfigurationError,
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
from .revaluation import AccountRevaluation, RevaluationSummary
from .service import Ledger, PostingRequest, TrialBalance, TrialBalanceLine
from .store import InMemoryLedgerStore, LedgerStore

__all__ = [
    "Account",
    "AccountClosedError",
    "AccountRevaluation",
    "AccountType",
    "Currency",
    "CurrencyMode",
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
    "MissingExchangeRateError",
    "PeriodClosedError",
    "Posting",
    "PostingRef",
    "PostingRequest",
    "PostingSummary",
    "Reconciler",
    "ReconciliationReport",
    "ReconciliationStore",
    "RevaluationConfigurationError",
    "RevaluationSummary",
    "Side",
    "StatementLine",
    "TrialBalance",
    "TrialBalanceLine",
    "UnbalancedEntryError",
    "UnknownAccountError",
    "UnknownPostingError",
    "validate_currency",
]
