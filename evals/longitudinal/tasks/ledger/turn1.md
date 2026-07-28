# ledger, turn 1

Build a double-entry ledger as a Python package `ledger/`.

- Accounts with a type (asset, liability, equity, revenue, expense) and a
  normal balance side.
- Journal entries made of two or more postings. An entry only commits if its
  debits and credits balance exactly.
- Balances per account, as of any point in time, and a trial balance across
  all accounts.
- Amounts in minor units, never floats.
- An in-memory store behind a narrow interface, so a database can replace it
  later without touching the domain.
- Reject: posting to a closed account, an unbalanced entry, an entry dated in
  a closed period.

Standard library only.
