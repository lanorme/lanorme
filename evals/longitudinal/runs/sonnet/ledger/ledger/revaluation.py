"""Result models for period-close foreign-currency revaluation.

``Ledger.close_period`` builds these when it is asked to revalue
foreign-currency balances; see ``ledger.service`` for the computation
itself.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal

from .currency import Currency
from .models import JournalEntry


@dataclasses.dataclass(frozen=True)
class AccountRevaluation:
    """One account's foreign-currency balance retranslated at a period's
    closing rate.

    ``native_balance`` is the account's own-currency balance as of the
    period end (debit-positive). ``prior_reporting_balance`` is what that
    balance was worth in the reporting currency as booked so far (from
    each entry's own historical rate, plus any earlier revaluation);
    ``new_reporting_balance`` is what it is worth at ``closing_rate``.
    ``delta`` is the unrealised gain or loss this account contributes,
    debit-positive, equal to ``new_reporting_balance -
    prior_reporting_balance``.
    """

    account_id: str
    currency: Currency
    closing_rate: decimal.Decimal
    native_balance: int
    prior_reporting_balance: int
    new_reporting_balance: int
    delta: int


@dataclasses.dataclass(frozen=True)
class RevaluationSummary:
    """The outcome of revaluing foreign-currency balances at a period close.

    ``gain_loss_amount`` is the net unrealised gain or loss posted to
    ``gain_loss_account_id``, debit-positive, in the reporting currency.
    ``entry`` is the journal entry that posted it (together with the
    offsetting per-account translation adjustments); it is ``None`` when
    every revalued account's delta was zero, so nothing needed posting.
    """

    period_end: datetime.date
    closing_rates: dict[Currency, decimal.Decimal]
    account_revaluations: tuple[AccountRevaluation, ...]
    gain_loss_account_id: str
    gain_loss_amount: int
    entry: JournalEntry | None
