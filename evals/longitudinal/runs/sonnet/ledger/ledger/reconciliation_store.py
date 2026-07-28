"""Persistence boundary for reconciliation decisions, and an in-memory
implementation.

A human's accept or reject of a proposed match must survive a rerun of
``Reconciler.reconcile`` -- otherwise a rejected pair would simply be
re-proposed on the next import of the same statement. ``ReconciliationStore``
is the narrow interface that makes that persistence swappable, exactly as
``LedgerStore`` does for the core ledger.
"""

from __future__ import annotations

from typing import Protocol

from .reconciliation_models import Match, MatchDecision, PostingRef

#: Uniquely identifies one (account, statement line, posting) pairing,
#: independent of which rule proposed it or what was decided about it.
DecisionKey = tuple[str, str, str, int]


def _decision_key(account_id: str, statement_external_id: str, posting_ref: PostingRef) -> DecisionKey:
    return (account_id, statement_external_id, posting_ref.entry_id, posting_ref.posting_index)


class ReconciliationStore(Protocol):
    """The persistence operations bank reconciliation relies on."""

    def save_decision(self, account_id: str, match: Match) -> None:
        """Record a human's decision (accept or reject) about one match.

        Keyed by the pairing (account, statement line, posting), so saving
        again for the same pairing overwrites the previous decision.
        """
        ...

    def get_decision(
        self, account_id: str, statement_external_id: str, posting_ref: PostingRef
    ) -> Match | None:
        """The previously recorded match for this exact pairing, if any."""
        ...

    def list_decisions(self, account_id: str) -> list[Match]:
        """Every decision recorded for this account, in no particular order."""
        ...


class InMemoryReconciliationStore:
    """A plain-dict ``ReconciliationStore`` that keeps everything in process
    memory."""

    def __init__(self) -> None:
        self._decisions: dict[DecisionKey, Match] = {}

    def save_decision(self, account_id: str, match: Match) -> None:
        key = _decision_key(account_id, match.statement_line.external_id, match.posting.ref)
        self._decisions[key] = match

    def get_decision(
        self, account_id: str, statement_external_id: str, posting_ref: PostingRef
    ) -> Match | None:
        key = _decision_key(account_id, statement_external_id, posting_ref)
        return self._decisions.get(key)

    def list_decisions(self, account_id: str) -> list[Match]:
        return [
            match
            for key, match in self._decisions.items()
            if key[0] == account_id
        ]
