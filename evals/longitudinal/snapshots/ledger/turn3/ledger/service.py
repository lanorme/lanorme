"""The ledger domain: opening accounts, posting entries, reading balances."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import uuid
from collections.abc import Iterable, Mapping

from .currency import Currency, CurrencyMode, convert_amount, validate_currency
from .errors import (
    AccountClosedError,
    InvalidEntryError,
    MissingExchangeRateError,
    PeriodClosedError,
    RevaluationConfigurationError,
    UnbalancedEntryError,
    UnknownAccountError,
)
from .models import Account, AccountType, JournalEntry, Posting, Side
from .revaluation import AccountRevaluation, RevaluationSummary
from .store import InMemoryLedgerStore, LedgerStore


@dataclasses.dataclass(frozen=True)
class PostingRequest:
    """One side of an entry to be posted; not yet validated or committed.

    ``amount`` is always in the target account's own currency -- there is
    no separate currency field here because the account it names is the
    single source of truth for that.
    """

    account_id: str
    side: Side
    amount: int


@dataclasses.dataclass(frozen=True)
class TrialBalanceLine:
    """One account's row in a trial balance.

    ``currency`` is the account's own currency when the trial balance was
    requested in ``CurrencyMode.ACCOUNT``, or the ledger's reporting
    currency when requested in ``CurrencyMode.REPORTING``.
    """

    account_id: str
    account_name: str
    currency: Currency
    debit: int
    credit: int


@dataclasses.dataclass(frozen=True)
class TrialBalance:
    """A full trial balance across every account, as of a point in time.

    In ``CurrencyMode.REPORTING`` every line is in the same currency, so
    ``is_balanced`` is guaranteed true for a ledger with no bugs. In
    ``CurrencyMode.ACCOUNT`` lines can be in different currencies; summing
    across them is still exact arithmetic, but a multi-currency entry only
    has to balance in the reporting currency (at its own rate), not in raw
    native units, so ``is_balanced`` need not hold once such entries exist.
    """

    as_of: datetime.date | None
    currency: CurrencyMode
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

    Holds almost no state of its own -- everything lives behind the
    injected ``LedgerStore``, so a persistent store can replace the
    in-memory one without any change here -- except the reporting
    currency, which is a ledger-wide setting rather than per-record data:
    it is what multi-currency entries must balance in, what period-close
    revaluation restates foreign balances into, and one of the two views
    ``balance``/``trial_balance`` can report in.
    """

    def __init__(
        self,
        store: LedgerStore | None = None,
        reporting_currency: Currency = "USD",
    ) -> None:
        validate_currency(reporting_currency)
        self._store: LedgerStore = store if store is not None else InMemoryLedgerStore()
        self._reporting_currency: Currency = reporting_currency

    @property
    def reporting_currency(self) -> Currency:
        """The currency multi-currency entries balance in and reporting-
        currency queries convert to."""
        return self._reporting_currency

    # -- accounts ---------------------------------------------------------

    def open_account(
        self,
        name: str,
        account_type: AccountType,
        currency: Currency | None = None,
    ) -> Account:
        """Create and store a new, open account.

        ``currency`` defaults to the ledger's reporting currency, so a
        single-currency ledger never needs to think about currencies at
        all.
        """
        account_currency = currency if currency is not None else self._reporting_currency
        validate_currency(account_currency)
        account = Account(
            id=str(uuid.uuid4()), name=name, type=account_type, currency=account_currency
        )
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

    def close_period(
        self,
        start: datetime.date,
        end: datetime.date,
        closing_rates: Mapping[Currency, decimal.Decimal] | None = None,
        unrealized_gain_loss_account_id: str | None = None,
    ) -> RevaluationSummary | None:
        """Close the inclusive date range [start, end] to new postings.

        If ``closing_rates`` is given, first revalues every foreign-
        currency account's balance as of ``end`` at its closing rate (one
        entry per currency present in ``closing_rates``), posts the net
        unrealised gain or loss to ``unrealized_gain_loss_account_id``, and
        returns a ``RevaluationSummary`` describing what happened. With no
        ``closing_rates`` (the default) this only freezes the period and
        returns ``None``, exactly as before multi-currency support existed.
        """
        if end < start:
            raise ValueError(f"period end {end} is before start {start}")
        summary = None
        if closing_rates:
            summary = self._revalue_period(
                end, dict(closing_rates), unrealized_gain_loss_account_id
            )
        self._store.add_closed_period(start, end)
        return summary

    def _revalue_period(
        self,
        end: datetime.date,
        closing_rates: dict[Currency, decimal.Decimal],
        gain_loss_account_id: str | None,
    ) -> RevaluationSummary:
        if gain_loss_account_id is None:
            raise RevaluationConfigurationError(
                "closing_rates given but no unrealized_gain_loss_account_id configured"
            )
        gain_loss_account = self._require_account(gain_loss_account_id)
        if gain_loss_account.currency != self._reporting_currency:
            raise RevaluationConfigurationError(
                f"unrealized gain/loss account {gain_loss_account_id!r} is in "
                f"{gain_loss_account.currency}, not the reporting currency "
                f"{self._reporting_currency}"
            )

        revaluations = self._compute_revaluations(end, closing_rates, gain_loss_account_id)
        net_debit_total = sum(reval.delta for reval in revaluations)

        if not revaluations:
            return RevaluationSummary(
                period_end=end,
                closing_rates=dict(closing_rates),
                account_revaluations=(),
                gain_loss_account_id=gain_loss_account_id,
                gain_loss_amount=0,
                entry=None,
            )

        postings = [
            Posting(
                account_id=reval.account_id,
                side=Side.DEBIT if reval.delta > 0 else Side.CREDIT,
                amount=abs(reval.delta),
            )
            for reval in revaluations
        ]
        if net_debit_total != 0:
            postings.append(
                Posting(
                    account_id=gain_loss_account_id,
                    side=Side.CREDIT if net_debit_total > 0 else Side.DEBIT,
                    amount=abs(net_debit_total),
                )
            )

        entry = JournalEntry(
            id=str(uuid.uuid4()),
            date=end,
            memo=f"Period close revaluation as of {end.isoformat()}",
            postings=tuple(postings),
            is_revaluation=True,
        )
        self._store.save_entry(entry)

        return RevaluationSummary(
            period_end=end,
            closing_rates=dict(closing_rates),
            account_revaluations=tuple(revaluations),
            gain_loss_account_id=gain_loss_account_id,
            gain_loss_amount=-net_debit_total,
            entry=entry,
        )

    def _compute_revaluations(
        self,
        end: datetime.date,
        closing_rates: dict[Currency, decimal.Decimal],
        gain_loss_account_id: str,
    ) -> list[AccountRevaluation]:
        revaluations = []
        for account in self._store.list_accounts():
            if account.id == gain_loss_account_id:
                continue
            rate = closing_rates.get(account.currency)
            if rate is None or account.currency == self._reporting_currency:
                continue
            native_debits, native_credits = self._sums_for(account, end, CurrencyMode.ACCOUNT)
            native_balance = native_debits - native_credits
            if native_balance == 0:
                continue
            prior_debits, prior_credits = self._sums_for(account, end, CurrencyMode.REPORTING)
            prior_reporting_balance = prior_debits - prior_credits
            new_reporting_balance = convert_amount(native_balance, rate)
            delta = new_reporting_balance - prior_reporting_balance
            if delta == 0:
                continue
            revaluations.append(
                AccountRevaluation(
                    account_id=account.id,
                    currency=account.currency,
                    closing_rate=decimal.Decimal(rate),
                    native_balance=native_balance,
                    prior_reporting_balance=prior_reporting_balance,
                    new_reporting_balance=new_reporting_balance,
                    delta=delta,
                )
            )
        return revaluations

    # -- journal entries ------------------------------------------------------

    def post(
        self,
        date: datetime.date,
        memo: str,
        postings: Iterable[PostingRequest],
        rates: Mapping[Currency, decimal.Decimal] | None = None,
    ) -> JournalEntry:
        """Validate and commit a balanced journal entry.

        Nothing is written to the store unless every check passes: at
        least two postings, every account known, an exchange rate on hand
        for every non-reporting-currency account touched, debits equal to
        credits (in the reporting currency, at the entry's rate, if more
        than one currency is involved -- otherwise in native units),
        every account open, and the entry's date outside any closed
        period. Returns the committed ``JournalEntry`` on success.

        ``rates`` gives, for each non-reporting-currency account this
        entry touches, the rate (reporting-currency units per one unit of
        that currency) to translate its postings by. It is required
        whenever any posting targets such an account, even if every
        posting shares one foreign currency and so does not need
        translating to prove the entry balances -- the rate is still
        recorded on the entry so a reporting-currency balance or trial
        balance can later reconstruct what it was booked as worth.
        """
        posting_requests = list(postings)
        self._validate_structure(posting_requests)
        entry_postings = tuple(
            Posting(account_id=request.account_id, side=request.side, amount=request.amount)
            for request in posting_requests
        )
        accounts = {
            posting.account_id: self._require_account(posting.account_id)
            for posting in entry_postings
        }
        fx_rates = dict(rates) if rates else {}
        self._validate_rates_present(entry_postings, accounts, fx_rates)
        self._validate_balanced(entry_postings, accounts, fx_rates)
        self._validate_accounts_open(entry_postings, accounts)
        self._validate_period_open(date)

        entry = JournalEntry(
            id=str(uuid.uuid4()), date=date, memo=memo, postings=entry_postings, rates=fx_rates
        )
        self._store.save_entry(entry)
        return entry

    # -- balances -------------------------------------------------------------

    def balance(
        self,
        account_id: str,
        as_of: datetime.date | None = None,
        currency: CurrencyMode = CurrencyMode.ACCOUNT,
    ) -> int:
        """The account's balance, signed per its normal side.

        A positive result means the account carries its normal balance
        (e.g. a debit balance for an asset account); a negative result
        means it is running the opposite way. ``as_of`` restricts the
        computation to entries dated on or before that date; ``None``
        (the default) considers every entry ever posted. ``currency``
        selects whether the result is in the account's own currency (the
        default) or converted to the ledger's reporting currency.
        """
        account = self._require_account(account_id)
        debits, credits = self._sums_for(account, as_of, currency)
        if account.normal_balance is Side.DEBIT:
            return debits - credits
        return credits - debits

    def trial_balance(
        self,
        as_of: datetime.date | None = None,
        currency: CurrencyMode = CurrencyMode.ACCOUNT,
    ) -> TrialBalance:
        """A trial balance across every account, as of a point in time,
        reported in either each account's own currency or the ledger's
        reporting currency (see ``CurrencyMode``)."""
        lines = []
        for account in self._store.list_accounts():
            debits, credits = self._sums_for(account, as_of, currency)
            net = debits - credits
            debit_column = net if net >= 0 else 0
            credit_column = -net if net < 0 else 0
            line_currency = (
                self._reporting_currency if currency is CurrencyMode.REPORTING else account.currency
            )
            lines.append(
                TrialBalanceLine(
                    account_id=account.id,
                    account_name=account.name,
                    currency=line_currency,
                    debit=debit_column,
                    credit=credit_column,
                )
            )
        lines.sort(key=lambda line: line.account_name)
        return TrialBalance(as_of=as_of, currency=currency, lines=tuple(lines))

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

    def _validate_rates_present(
        self,
        postings: tuple[Posting, ...],
        accounts: dict[str, Account],
        rates: dict[Currency, decimal.Decimal],
    ) -> None:
        required = {
            accounts[posting.account_id].currency
            for posting in postings
            if accounts[posting.account_id].currency != self._reporting_currency
        }
        missing = sorted(currency for currency in required if currency not in rates)
        if missing:
            raise MissingExchangeRateError(
                f"missing exchange rate(s) to {self._reporting_currency} for {missing}"
            )

    def _validate_balanced(
        self,
        postings: tuple[Posting, ...],
        accounts: dict[str, Account],
        rates: dict[Currency, decimal.Decimal],
    ) -> None:
        currencies_used = {accounts[posting.account_id].currency for posting in postings}
        if len(currencies_used) == 1:
            debits = sum(posting.amount for posting in postings if posting.side is Side.DEBIT)
            credits = sum(posting.amount for posting in postings if posting.side is Side.CREDIT)
            if debits != credits:
                raise UnbalancedEntryError(
                    f"entry does not balance: debits={debits}, credits={credits}"
                )
            return

        debits = credits = 0
        for posting in postings:
            currency = accounts[posting.account_id].currency
            converted = self._to_reporting(posting.amount, currency, rates)
            if posting.side is Side.DEBIT:
                debits += converted
            else:
                credits += converted
        if debits != credits:
            raise UnbalancedEntryError(
                f"entry does not balance in {self._reporting_currency} at its rate: "
                f"debits={debits}, credits={credits}"
            )

    def _to_reporting(
        self, amount: int, currency: Currency, rates: dict[Currency, decimal.Decimal]
    ) -> int:
        if currency == self._reporting_currency:
            return amount
        return convert_amount(amount, rates[currency])

    @staticmethod
    def _validate_accounts_open(
        postings: tuple[Posting, ...], accounts: dict[str, Account]
    ) -> None:
        for posting in postings:
            account = accounts[posting.account_id]
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
        self, account: Account, as_of: datetime.date | None, currency: CurrencyMode
    ) -> tuple[int, int]:
        debits = 0
        credits = 0
        for entry in self._store.list_entries():
            if as_of is not None and entry.date > as_of:
                continue
            for posting in entry.postings:
                if posting.account_id != account.id:
                    continue
                if currency is CurrencyMode.ACCOUNT:
                    if entry.is_revaluation and account.currency != self._reporting_currency:
                        # This posting's amount is in the reporting
                        # currency, not this account's own -- it only
                        # belongs in the reporting-currency view.
                        continue
                    amount = posting.amount
                else:
                    amount = self._reporting_amount(posting, entry, account)
                if posting.side is Side.DEBIT:
                    debits += amount
                else:
                    credits += amount
        return debits, credits

    def _reporting_amount(self, posting: Posting, entry: JournalEntry, account: Account) -> int:
        if entry.is_revaluation or account.currency == self._reporting_currency:
            return posting.amount
        rate = entry.rates.get(account.currency)
        if rate is None:
            raise MissingExchangeRateError(
                f"entry {entry.id!r} has no recorded exchange rate for {account.currency}"
            )
        return convert_amount(posting.amount, rate)
