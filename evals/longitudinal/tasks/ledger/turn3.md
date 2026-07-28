# ledger, turn 3

Extend the existing `ledger/` package in this directory. Do not rewrite it.

Add multi-currency.

- Every account carries a currency. Postings are in the account's currency.
- An entry may span currencies provided it balances in the reporting currency
  at the entry's rate.
- Period close: revalue foreign-currency balances at the closing rate, post
  the unrealised gain or loss to a configured account, and freeze the period.
- Trial balance and reconciliation must both keep working, reported in either
  the account currency or the reporting currency.
