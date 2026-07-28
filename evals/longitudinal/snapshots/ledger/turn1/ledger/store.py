"""Storage boundary for the ledger, and an in-memory implementation.

The domain layer (``ledger.service.Ledger``) only ever talks to a
``LedgerStore``. A persistent backend (a database, a file, ...) can be
substituted later by implementing the same narrow interface, without any
change to the domain logic itself.
"""

from __future__ import annotations

import datetime
from typing import Protocol

from .models import Account, JournalEntry


class LedgerStore(Protocol):
    """The persistence operations the ledger domain relies on."""

    def save_account(self, account: Account) -> None:
        """Create or overwrite an account record, keyed by its id."""
        ...

    def get_account(self, account_id: str) -> Account | None:
        """Look up a single account by id, or ``None`` if it does not exist."""
        ...

    def list_accounts(self) -> list[Account]:
        """Every known account, in no particular order."""
        ...

    def save_entry(self, entry: JournalEntry) -> None:
        """Persist a committed journal entry. Entries are never mutated."""
        ...

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        """Look up a single journal entry by id, or ``None`` if it does not exist."""
        ...

    def list_entries(self) -> list[JournalEntry]:
        """Every committed journal entry, in no particular order."""
        ...

    def add_closed_period(self, start: datetime.date, end: datetime.date) -> None:
        """Record the inclusive range [start, end] as closed to new postings."""
        ...

    def list_closed_periods(self) -> list[tuple[datetime.date, datetime.date]]:
        """All closed date ranges recorded so far."""
        ...


class InMemoryLedgerStore:
    """A plain-dict ``LedgerStore`` that keeps everything in process memory."""

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}
        self._entries: dict[str, JournalEntry] = {}
        self._closed_periods: list[tuple[datetime.date, datetime.date]] = []

    def save_account(self, account: Account) -> None:
        self._accounts[account.id] = account

    def get_account(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def list_accounts(self) -> list[Account]:
        return list(self._accounts.values())

    def save_entry(self, entry: JournalEntry) -> None:
        self._entries[entry.id] = entry

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        return self._entries.get(entry_id)

    def list_entries(self) -> list[JournalEntry]:
        return list(self._entries.values())

    def add_closed_period(self, start: datetime.date, end: datetime.date) -> None:
        self._closed_periods.append((start, end))

    def list_closed_periods(self) -> list[tuple[datetime.date, datetime.date]]:
        return list(self._closed_periods)
