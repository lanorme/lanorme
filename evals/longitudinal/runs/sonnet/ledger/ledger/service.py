"""The ledger domain: opening accounts, posting entries, reading balances."""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from collections.abc import Iterable

from .errors import (
    AccountClosedError,
    InvalidEntryError,
    PeriodClosedError,
    UnbalancedEntryError,
    UnknownAccountError,
)
from .models import Account, AccountType, JournalEntry, Posting, Side
from .store import InMemoryLedgerStore, LedgerStore


@dataclasses.dataclass(frozen=True)
class PostingRequest:
    """One side of an entry to be posted; not yet validated or committed."""

    account_id: str
    side: Side
    amount: int


@dataclasses.dataclass(frozen=True)
class TrialBalanceLine:
    """One account's row in a trial balance."""

    account_id: str
    account_name: str
    debit: int
    credit: int


@dataclasses.dataclass(frozen=True)
class TrialBalance:
    """A full trial balance across every account, as of a point in time."""

    as_of: datetime.date | None
    lines: tuple[TrialBalanceLine, ...]

    @property
    def total_debits(self) -> int:
        """Sum of the debit column, in minor units."""
        return sum(line.debit for line in self.lines)

    @property
    def total_credits(self) -> int:
        """Sum of the credit column, in minor units."""
        return sum(line.credit for line in self.lines)

    @property
    def is_balanced(self) -> bool:
        """Whether the debit and credit columns total exactly the same."""
        return self.total_debits == self.total_credits


class Ledger:
    """Application-level double-entry ledger.

    Holds no state of its own; everything lives behind the injected
    ``LedgerStore``, so a persistent store can replace the in-memory one
    without any change here.
    """

    def __init__(self, store: LedgerStore | None = None) -> None:
        self._store: LedgerStore = store if store is not None else InMemoryLedgerStore()

    # -- accounts ---------------------------------------------------------

    def open_account(self, name: str, account_type: AccountType) -> Account:
        """Create and store a new, open account."""
        account = Account(id=str(uuid.uuid4()), name=name, type=account_type)
        self._store.save_account(account)
        return account

    def close_account(self, account_id: str) -> None:
        """Close an account so it can no longer receive new postings."""
        account = self._require_account(account_id)
        self._store.save_account(dataclasses.replace(account, closed=True))

    def get_account(self, account_id: str) -> Account:
        """Fetch a single account by id, raising if it does not exist."""
        return self._require_account(account_id)

    def accounts(self) -> list[Account]:
        """Every account known to the ledger."""
        return self._store.list_accounts()

    def postings_for_account(
        self, account_id: str
    ) -> list[tuple[JournalEntry, int, Posting]]:
        """Every posting against this account, with its entry and index.

        The index is the posting's position within ``entry.postings``;
        ``Posting`` itself carries no id, so ``(entry.id, index)`` is the
        smallest handle that identifies one specific posting, which is what
        bank reconciliation needs to refer back to a match.
        """
        self._require_account(account_id)
        return [
            (entry, index, posting)
            for entry in self._store.list_entries()
            for index, posting in enumerate(entry.postings)
            if posting.account_id == account_id
        ]

    # -- periods ------------------------------------------------------------

    def close_period(self, start: datetime.date, end: datetime.date) -> None:
        """Close the inclusive date range [start, end] to new postings."""
        if end < start:
            raise ValueError(f"period end {end} is before start {start}")
        self._store.add_closed_period(start, end)

    # -- journal entries ------------------------------------------------------

    def post(
        self,
        date: datetime.date,
        memo: str,
        postings: Iterable[PostingRequest],
    ) -> JournalEntry:
        """Validate and commit a balanced journal entry.

        Nothing is written to the store unless every check passes: at
        least two postings, debits equal to credits, every account open,
        and the entry's date outside any closed period. Returns the
        committed ``JournalEntry`` on success.
        """
        posting_requests = list(postings)
        self._validate_structure(posting_requests)
        entry_postings = tuple(
            Posting(account_id=request.account_id, side=request.side, amount=request.amount)
            for request in posting_requests
        )
        self._validate_balanced(entry_postings)
        self._validate_accounts_open(entry_postings)
        self._validate_period_open(date)

        entry = JournalEntry(
            id=str(uuid.uuid4()), date=date, memo=memo, postings=entry_postings
        )
        self._store.save_entry(entry)
        return entry

    # -- balances -------------------------------------------------------------

    def balance(self, account_id: str, as_of: datetime.date | None = None) -> int:
        """The account's balance in minor units, signed per its normal side.

        A positive result means the account carries its normal balance
        (e.g. a debit balance for an asset account); a negative result
        means it is running the opposite way. ``as_of`` restricts the
        computation to entries dated on or before that date; ``None``
        (the default) considers every entry ever posted.
        """
        account = self._require_account(account_id)
        debits, credits = self._sums_for(account_id, as_of)
        if account.normal_balance is Side.DEBIT:
            return debits - credits
        return credits - debits

    def trial_balance(self, as_of: datetime.date | None = None) -> TrialBalance:
        """A trial balance across every account, as of a point in time."""
        lines = []
        for account in self._store.list_accounts():
            debits, credits = self._sums_for(account.id, as_of)
            net = debits - credits
            debit_column = net if net >= 0 else 0
            credit_column = -net if net < 0 else 0
            lines.append(
                TrialBalanceLine(
                    account_id=account.id,
                    account_name=account.name,
                    debit=debit_column,
                    credit=credit_column,
                )
            )
        lines.sort(key=lambda line: line.account_name)
        return TrialBalance(as_of=as_of, lines=tuple(lines))

    # -- internals --------------------------------------------------------------

    def _require_account(self, account_id: str) -> Account:
        account = self._store.get_account(account_id)
        if account is None:
            raise UnknownAccountError(f"no such account: {account_id!r}")
        return account

    @staticmethod
    def _validate_structure(postings: list[PostingRequest]) -> None:
        if len(postings) < 2:
            raise InvalidEntryError(
                f"an entry needs at least two postings, got {len(postings)}"
            )

    @staticmethod
    def _validate_balanced(postings: tuple[Posting, ...]) -> None:
        debits = sum(posting.amount for posting in postings if posting.side is Side.DEBIT)
        credits = sum(posting.amount for posting in postings if posting.side is Side.CREDIT)
        if debits != credits:
            raise UnbalancedEntryError(
                f"entry does not balance: debits={debits}, credits={credits}"
            )

    def _validate_accounts_open(self, postings: tuple[Posting, ...]) -> None:
        for posting in postings:
            account = self._require_account(posting.account_id)
            if account.closed:
                raise AccountClosedError(
                    f"account {account.id!r} ({account.name}) is closed"
                )

    def _validate_period_open(self, date: datetime.date) -> None:
        for start, end in self._store.list_closed_periods():
            if start <= date <= end:
                raise PeriodClosedError(
                    f"period [{start}, {end}] is closed; cannot post on {date}"
                )

    def _sums_for(
        self, account_id: str, as_of: datetime.date | None
    ) -> tuple[int, int]:
        debits = 0
        credits = 0
        for entry in self._store.list_entries():
            if as_of is not None and entry.date > as_of:
                continue
            for posting in entry.postings:
                if posting.account_id != account_id:
                    continue
                if posting.side is Side.DEBIT:
                    debits += posting.amount
                else:
                    credits += posting.amount
        return debits, credits
