"""Bank reconciliation: matching statement lines against ledger postings.

Typical usage::

    from ledger import Reconciler, StatementLine

    reconciler = Reconciler(book)
    report = reconciler.reconcile(
        cash.id,
        statement_lines=[
            StatementLine(date(2026, 1, 6), 15_000, "INV1001 PAYMT", "STMT-1"),
        ],
        date_window_days=3,
    )

    for match in report.matches:
        reconciler.accept(cash.id, match)   # or .reject(...)

A rerun of ``reconcile`` on the same account will not re-propose a pairing
that was rejected, and will keep re-presenting an accepted pairing as
``ACCEPTED`` rather than proposing it again from scratch.
"""

from __future__ import annotations

import dataclasses
import decimal
import difflib
from collections.abc import Callable, Iterable

from .currency import CurrencyMode, convert_amount
from .errors import MissingExchangeRateError, UnknownPostingError
from .models import Account, Posting
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
from .service import Ledger

# A candidate pairing's rank: lower sorts first (is preferred). The trailing
# three fields are a deterministic tie-break so results never depend on
# dict/list iteration order.
_RankKey = tuple[float, str, str, int]


def _signed_amount(posting: Posting, account: Account) -> int:
    """A posting's amount, signed per the statement's convention: positive
    when it moves the account in its normal direction, negative otherwise.
    """
    return posting.amount if posting.side is account.normal_balance else -posting.amount


def _description_similarity(a: str, b: str) -> float:
    """A word-order-insensitive fuzzy similarity between two descriptions,
    in ``[0.0, 1.0]``.

    Bank descriptions routinely reorder or abbreviate the words of a
    ledger memo (``"INV1001 PAYMT"`` vs. ``"Invoice 1001 payment"``), which
    a plain character-sequence ratio penalises heavily. Taking the better
    of the direct ratio and the ratio on sorted words keeps the check
    forgiving of reordering while still catching near-identical text.
    """
    a = a.casefold()
    b = b.casefold()
    direct = difflib.SequenceMatcher(None, a, b).ratio()
    by_word = difflib.SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(direct, by_word)


def _tie_break(external_id: str, ref: PostingRef) -> tuple[str, str, int]:
    return (external_id, ref.entry_id, ref.posting_index)


class Reconciler:
    """Matches an imported bank statement against one account's ledger
    postings, and records a human's accept/reject decisions about the
    proposed matches.

    Holds no ledger state of its own; ``Ledger`` is the source of truth for
    postings, and everything reconciliation-specific lives behind the
    injected ``ReconciliationStore``.
    """

    def __init__(self, ledger: Ledger, store: ReconciliationStore | None = None) -> None:
        self._ledger = ledger
        self._store: ReconciliationStore = (
            store if store is not None else InMemoryReconciliationStore()
        )

    # -- reconciling --------------------------------------------------------

    def reconcile(
        self,
        account_id: str,
        statement_lines: Iterable[StatementLine],
        date_window_days: int = 3,
        fuzzy_threshold: float = 0.6,
        currency: CurrencyMode = CurrencyMode.ACCOUNT,
        rate: decimal.Decimal | None = None,
    ) -> ReconciliationReport:
        """Match a bank statement against this account's ledger postings.

        Runs three passes in order, each over whatever is still unmatched
        after the previous one: an exact amount-and-date match, an
        exact-amount match within ``date_window_days`` days, then a fuzzy
        match on description (still requiring the exact amount) for pairs
        scoring at least ``fuzzy_threshold`` similarity. A statement line
        matches at most one posting and vice versa.

        Pairings already accepted in a previous run are re-affirmed
        directly, without being re-derived from amounts or dates. Pairings
        already rejected are never proposed again, though both sides
        remain free to match something else.

        Matching itself always happens in the account's own currency, since
        that is what both the bank statement and the ledger's postings are
        actually denominated in. ``currency`` only affects how the
        *returned* report's amounts are expressed: with
        ``CurrencyMode.REPORTING`` every figure is converted through
        ``rate`` (reporting-currency units per one unit of the account's
        currency) into the ledger's reporting currency. ``rate`` is
        required whenever the account's currency differs from the
        ledger's; a same-currency account needs none. This is purely a
        presentation transform -- match identities (the statement line's
        ``external_id`` and the posting's ``ref``) are untouched, so a
        report requested in reporting currency can still be passed to
        ``accept``/``reject`` exactly like the native one.
        """
        account = self._ledger.get_account(account_id)
        remaining_lines = {line.external_id: line for line in statement_lines}
        remaining_postings = self._posting_summaries(account_id, account)

        matches = self._apply_accepted(account_id, remaining_lines, remaining_postings)
        rejected = self._rejected_pairs(account_id)

        matches += self._match_pass(
            remaining_lines,
            remaining_postings,
            rejected,
            rule=MatchRule.EXACT,
            scorer=lambda line, posting: (
                0.0
                if posting.date == line.date and posting.signed_amount == line.amount_minor
                else None
            ),
        )
        matches += self._match_pass(
            remaining_lines,
            remaining_postings,
            rejected,
            rule=MatchRule.DATE_WINDOW,
            scorer=lambda line, posting: self._date_window_score(
                line, posting, date_window_days
            ),
        )
        matches += self._match_pass(
            remaining_lines,
            remaining_postings,
            rejected,
            rule=MatchRule.FUZZY_DESCRIPTION,
            scorer=lambda line, posting: self._fuzzy_score(line, posting, fuzzy_threshold),
        )

        report = ReconciliationReport(
            account_id=account_id,
            matches=tuple(matches),
            unmatched_statement_lines=tuple(remaining_lines.values()),
            unmatched_postings=tuple(remaining_postings.values()),
            closing_difference=self._closing_difference(remaining_lines, remaining_postings),
        )
        if currency is CurrencyMode.REPORTING:
            report = self._in_reporting_currency(report, account, rate)
        return report

    # -- human decisions ------------------------------------------------------

    def accept(self, account_id: str, match: Match) -> Match:
        """Record a human's acceptance of a proposed match.

        Persisted so future ``reconcile`` calls re-affirm this exact
        pairing directly, rather than needing to re-derive it.
        """
        self._require_real_posting(account_id, match.posting.ref)
        accepted = dataclasses.replace(match, decision=MatchDecision.ACCEPTED)
        self._store.save_decision(account_id, accepted)
        return accepted

    def reject(self, account_id: str, match: Match) -> Match:
        """Record a human's rejection of a proposed match.

        Persisted so future ``reconcile`` calls never propose this exact
        statement-line/posting pairing again; both sides stay eligible to
        match something else.
        """
        self._require_real_posting(account_id, match.posting.ref)
        rejected = dataclasses.replace(match, decision=MatchDecision.REJECTED)
        self._store.save_decision(account_id, rejected)
        return rejected

    def decisions(self, account_id: str) -> list[Match]:
        """Every decision (accepted or rejected) recorded for this account."""
        return self._store.list_decisions(account_id)

    # -- matching passes --------------------------------------------------------

    @staticmethod
    def _match_pass(
        remaining_lines: dict[str, StatementLine],
        remaining_postings: dict[PostingRef, PostingSummary],
        rejected: set[tuple[str, PostingRef]],
        rule: MatchRule,
        scorer: Callable[[StatementLine, PostingSummary], float | None],
    ) -> list[Match]:
        """Greedily pair whatever remains under one match rule.

        ``scorer`` returns a rank (lower is a better match) for a candidate
        pair, or ``None`` if the pair is not a candidate at all under this
        rule. Candidates are considered best-first; once a line or posting
        is claimed it drops out of consideration for the rest of the pass.
        """
        candidates: list[tuple[_RankKey, str, PostingRef]] = []
        for external_id, line in remaining_lines.items():
            for ref, posting in remaining_postings.items():
                if (external_id, ref) in rejected:
                    continue
                rank = scorer(line, posting)
                if rank is not None:
                    candidates.append((rank, *_tie_break(external_id, ref)))
        candidates.sort()

        made: list[Match] = []
        for _rank, external_id, entry_id, posting_index in candidates:
            ref = PostingRef(entry_id, posting_index)
            if external_id not in remaining_lines or ref not in remaining_postings:
                continue
            line = remaining_lines.pop(external_id)
            posting = remaining_postings.pop(ref)
            made.append(
                Match(
                    statement_line=line,
                    posting=posting,
                    rule=rule,
                    decision=MatchDecision.PROPOSED,
                )
            )
        return made

    @staticmethod
    def _date_window_score(
        line: StatementLine, posting: PostingSummary, date_window_days: int
    ) -> float | None:
        if posting.signed_amount != line.amount_minor:
            return None
        day_diff = abs((posting.date - line.date).days)
        if day_diff > date_window_days:
            return None
        return float(day_diff)

    @staticmethod
    def _fuzzy_score(
        line: StatementLine, posting: PostingSummary, fuzzy_threshold: float
    ) -> float | None:
        if posting.signed_amount != line.amount_minor:
            return None
        ratio = _description_similarity(line.description, posting.memo)
        if ratio < fuzzy_threshold:
            return None
        return 1.0 - ratio

    # -- persisted decisions --------------------------------------------------

    def _apply_accepted(
        self,
        account_id: str,
        remaining_lines: dict[str, StatementLine],
        remaining_postings: dict[PostingRef, PostingSummary],
    ) -> list[Match]:
        matches: list[Match] = []
        for decision in self._store.list_decisions(account_id):
            if decision.decision is not MatchDecision.ACCEPTED:
                continue
            external_id = decision.statement_line.external_id
            ref = decision.posting.ref
            if external_id not in remaining_lines or ref not in remaining_postings:
                continue
            line = remaining_lines.pop(external_id)
            posting = remaining_postings.pop(ref)
            matches.append(
                Match(
                    statement_line=line,
                    posting=posting,
                    rule=decision.rule,
                    decision=MatchDecision.ACCEPTED,
                )
            )
        return matches

    def _rejected_pairs(self, account_id: str) -> set[tuple[str, PostingRef]]:
        return {
            (decision.statement_line.external_id, decision.posting.ref)
            for decision in self._store.list_decisions(account_id)
            if decision.decision is MatchDecision.REJECTED
        }

    # -- currency -------------------------------------------------------------

    def _in_reporting_currency(
        self, report: ReconciliationReport, account: Account, rate: decimal.Decimal | None
    ) -> ReconciliationReport:
        effective_rate = self._resolve_rate(account, rate)
        if effective_rate == 1:
            return report

        converted_lines = tuple(
            self._convert_line(line, effective_rate)
            for line in report.unmatched_statement_lines
        )
        converted_postings = tuple(
            self._convert_posting(posting, effective_rate)
            for posting in report.unmatched_postings
        )
        matches = tuple(
            dataclasses.replace(
                match,
                statement_line=self._convert_line(match.statement_line, effective_rate),
                posting=self._convert_posting(match.posting, effective_rate),
            )
            for match in report.matches
        )
        closing_difference = self._closing_difference(
            {line.external_id: line for line in converted_lines},
            {posting.ref: posting for posting in converted_postings},
        )
        return dataclasses.replace(
            report,
            matches=matches,
            unmatched_statement_lines=converted_lines,
            unmatched_postings=converted_postings,
            closing_difference=closing_difference,
        )

    def _resolve_rate(
        self, account: Account, rate: decimal.Decimal | None
    ) -> decimal.Decimal:
        if account.currency == self._ledger.reporting_currency:
            return decimal.Decimal(1)
        if rate is None:
            raise MissingExchangeRateError(
                f"account {account.id!r} ({account.name}) is in {account.currency}; "
                f"pass a rate to report in {self._ledger.reporting_currency}"
            )
        return decimal.Decimal(rate)

    @staticmethod
    def _convert_line(line: StatementLine, rate: decimal.Decimal) -> StatementLine:
        return dataclasses.replace(line, amount_minor=convert_amount(line.amount_minor, rate))

    @staticmethod
    def _convert_posting(posting: PostingSummary, rate: decimal.Decimal) -> PostingSummary:
        return dataclasses.replace(
            posting, signed_amount=convert_amount(posting.signed_amount, rate)
        )

    # -- internals --------------------------------------------------------------

    def _posting_summaries(
        self, account_id: str, account: Account
    ) -> dict[PostingRef, PostingSummary]:
        summaries = {}
        for entry, index, posting in self._ledger.postings_for_account(account_id):
            if entry.is_revaluation:
                # A revaluation posting is a book-only translation
                # adjustment, not a real cash movement, so it can never
                # correspond to a bank statement line.
                continue
            ref = PostingRef(entry.id, index)
            summaries[ref] = PostingSummary(
                ref=ref,
                date=entry.date,
                memo=entry.memo,
                signed_amount=_signed_amount(posting, account),
            )
        return summaries

    def _require_real_posting(self, account_id: str, ref: PostingRef) -> None:
        account = self._ledger.get_account(account_id)
        for entry, index, _posting in self._ledger.postings_for_account(account_id):
            if entry.id == ref.entry_id and index == ref.posting_index:
                return
        raise UnknownPostingError(
            f"no posting at entry {ref.entry_id!r} index {ref.posting_index} "
            f"on account {account_id!r} ({account.name})"
        )

    @staticmethod
    def _closing_difference(
        remaining_lines: dict[str, StatementLine],
        remaining_postings: dict[PostingRef, PostingSummary],
    ) -> int:
        lines_total = sum(line.amount_minor for line in remaining_lines.values())
        postings_total = sum(
            posting.signed_amount for posting in remaining_postings.values()
        )
        return lines_total - postings_total
