"""Domain models for bank reconciliation: statement lines, matches, reports."""

from __future__ import annotations

import dataclasses
import datetime
import enum


class MatchRule(enum.Enum):
    """Which pass of the matching algorithm produced a match."""

    EXACT = "exact"
    DATE_WINDOW = "date_window"
    FUZZY_DESCRIPTION = "fuzzy_description"


class MatchDecision(enum.Enum):
    """A human's disposition of a match, or the lack of one yet."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclasses.dataclass(frozen=True)
class StatementLine:
    """One line of an imported bank statement.

    ``amount_minor`` is signed, in whole minor units (e.g. cents): positive
    for money moving in the reconciled account's normal direction (e.g. a
    deposit into an asset account), negative for money moving the other way.
    ``external_id`` is the bank's own identifier for the line and is what
    match decisions are keyed on, so it must be stable across re-imports of
    the same statement.
    """

    date: datetime.date
    amount_minor: int
    description: str
    external_id: str


@dataclasses.dataclass(frozen=True)
class PostingRef:
    """A stable reference to one posting: its entry id and position in it.

    ``Posting`` itself carries no id of its own, so the pair (entry id,
    index within ``entry.postings``) is the smallest handle that identifies
    one specific posting -- which is what a persisted match decision needs
    to refer back to.
    """

    entry_id: str
    posting_index: int


@dataclasses.dataclass(frozen=True)
class PostingSummary:
    """Enough about one ledger posting to review a match without a further
    lookup against the ledger."""

    ref: PostingRef
    date: datetime.date
    memo: str
    signed_amount: int


@dataclasses.dataclass(frozen=True)
class Match:
    """A pairing between one statement line and one ledger posting."""

    statement_line: StatementLine
    posting: PostingSummary
    rule: MatchRule
    decision: MatchDecision


@dataclasses.dataclass(frozen=True)
class ReconciliationReport:
    """The result of reconciling a bank statement against one account's
    ledger postings.

    ``closing_difference`` is the signed amount by which the statement and
    the ledger still disagree once every match has been accounted for: the
    total of the unmatched statement lines minus the total of the unmatched
    postings (both in the statement's sign convention). It is zero exactly
    when everything on one side has a counterpart on the other.
    """

    account_id: str
    matches: tuple[Match, ...]
    unmatched_statement_lines: tuple[StatementLine, ...]
    unmatched_postings: tuple[PostingSummary, ...]
    closing_difference: int
