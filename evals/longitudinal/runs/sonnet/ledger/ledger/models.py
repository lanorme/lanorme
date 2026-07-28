"""Domain models for the double-entry ledger: accounts, postings, entries."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
from collections.abc import Mapping

from .currency import Currency, validate_currency


class AccountType(enum.Enum):
    """The five fundamental account types."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class Side(enum.Enum):
    """The two sides of a posting, or of an account's normal balance."""

    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def opposite(self) -> Side:
        """The other side."""
        return Side.CREDIT if self is Side.DEBIT else Side.DEBIT


_NORMAL_BALANCE: dict[AccountType, Side] = {
    AccountType.ASSET: Side.DEBIT,
    AccountType.LIABILITY: Side.CREDIT,
    AccountType.EQUITY: Side.CREDIT,
    AccountType.REVENUE: Side.CREDIT,
    AccountType.EXPENSE: Side.DEBIT,
}


def normal_balance(account_type: AccountType) -> Side:
    """Return the normal balance side (debit or credit) for an account type."""
    return _NORMAL_BALANCE[account_type]


@dataclasses.dataclass
class Account:
    """A ledger account.

    Every account carries a currency; its postings are always recorded in
    that currency (see ``Ledger.post``). A closed account keeps its history
    and balance but may no longer receive new postings.
    """

    id: str
    name: str
    type: AccountType
    currency: Currency
    closed: bool = False

    def __post_init__(self) -> None:
        validate_currency(self.currency)

    @property
    def normal_balance(self) -> Side:
        """The side on which this account normally carries a balance."""
        return normal_balance(self.type)


@dataclasses.dataclass(frozen=True)
class Posting:
    """One line of a journal entry: a signed movement against one account.

    Amounts are always whole numbers of minor units (e.g. cents), never
    floats, and always positive; the direction is carried by ``side``.
    """

    account_id: str
    side: Side
    amount: int

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypeError(
                "posting amount must be an int in minor units, "
                f"got {self.amount!r}"
            )
        if self.amount <= 0:
            raise ValueError(f"posting amount must be positive, got {self.amount}")


@dataclasses.dataclass(frozen=True)
class JournalEntry:
    """A committed, balanced journal entry made of two or more postings.

    ``rates`` records, for every non-reporting-currency account this entry
    touches, the rate (destination-currency units per one unit of that
    currency) used to translate its postings into the reporting currency --
    both to check that a multi-currency entry balances at post time, and
    later so a reporting-currency balance or trial balance can reconstruct
    what this entry was booked as worth. ``is_revaluation`` marks an entry
    built by period-close revaluation rather than by ``Ledger.post``: its
    postings restate an existing foreign-currency balance's reporting-
    currency equivalent rather than moving money, so they are excluded from
    an account-currency balance and from bank reconciliation.
    """

    id: str
    date: datetime.date
    memo: str
    postings: tuple[Posting, ...]
    rates: Mapping[Currency, decimal.Decimal] = dataclasses.field(default_factory=dict)
    is_revaluation: bool = False

    def total(self, side: Side) -> int:
        """Sum of this entry's postings on the given side, in minor units."""
        return sum(posting.amount for posting in self.postings if posting.side is side)

    @property
    def is_balanced(self) -> bool:
        """Whether this entry's debits and credits total exactly the same."""
        return self.total(Side.DEBIT) == self.total(Side.CREDIT)
