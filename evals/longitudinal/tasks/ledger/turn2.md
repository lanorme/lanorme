# ledger, turn 2

Extend the existing `ledger/` package in this directory. Do not rewrite it.

Add bank reconciliation.

- Import a bank statement: a list of `(date, amount_minor, description,
  external_id)`.
- Match statement lines against ledger postings: exact amount and date, then
  amount within a configurable date window, then a fuzzy pass on description.
  A statement line matches at most one posting and vice versa.
- Produce a reconciliation report: matched pairs with the rule that matched
  them, unmatched statement lines, unmatched ledger postings, and the closing
  difference.
- Let a human accept or reject a proposed match, and persist that decision so
  a rerun does not re-propose a rejected pair.
