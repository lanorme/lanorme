"""Currency codes, amount conversion, and the account/reporting mode used
when reading balances back out of the ledger.

Every account carries a currency and postings are always recorded in it.
A second, ledger-wide "reporting currency" is what multi-currency entries
must balance in, what period-close revaluation restates foreign balances
into, and one of the two views ``Ledger.balance``, ``Ledger.trial_balance``,
and ``Reconciler.reconcile`` can render their figures in.
"""

from __future__ import annotations

import decimal
import enum

Currency = str
"""An ISO-4217-style currency code, e.g. ``"USD"`` or ``"EUR"``.

Plain ``str`` rather than a closed enum: the ledger does not maintain a
fixed list of currencies, so any three-letter uppercase code is accepted.
"""


def validate_currency(currency: Currency) -> None:
    """Raise ``ValueError`` unless ``currency`` looks like a currency code.

    A currency code is exactly three uppercase ASCII letters (ISO 4217's
    alphabetic form, e.g. ``"USD"``, ``"EUR"``, ``"JPY"``).
    """
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise ValueError(
            f"currency must be a three-letter uppercase code, got {currency!r}"
        )


class CurrencyMode(enum.Enum):
    """Which currency a balance, trial balance, or reconciliation report is
    expressed in.

    ``ACCOUNT`` reports each account in its own currency (mixed currencies
    across a multi-currency ledger's accounts). ``REPORTING`` converts
    everything to the ledger's single reporting currency, so totals across
    every account can be compared and summed directly.
    """

    ACCOUNT = "account"
    REPORTING = "reporting"


def convert_amount(amount: int, rate: decimal.Decimal) -> int:
    """Convert ``amount`` (minor units) through ``rate`` to another
    currency's minor units, rounded to the nearest whole unit.

    ``rate`` is the number of destination-currency units per one
    source-currency unit; since both amounts are already scaled to minor
    units the ratio is the same as the major-unit exchange rate, provided
    both currencies share the same number of decimal places. Rounds half
    away from zero, the conventional rule for money.
    """
    rate = decimal.Decimal(rate)
    if rate <= 0:
        raise ValueError(f"exchange rate must be positive, got {rate}")
    converted = (decimal.Decimal(amount) * rate).quantize(
        decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
    )
    return int(converted)
