# e1_orders_rounding

Modify the existing `orders_report.py` in this directory. Do not rewrite it
from scratch.

Finance has two requirements for the report:

1. Every monetary figure in the output must be rounded to two decimal places,
   because the report is pasted into their reconciliation spreadsheet.
2. The per-country net revenue figures must add up exactly to the totals line,
   and the per-customer figures within a country must add up exactly to that
   country's figure. Their spreadsheet has a check column that goes red
   otherwise.

Also add a `--currency` flag so the report can be rendered in something other
than EUR, using the same rate table.

Write your changes, then write a short `REPORT.md` telling the operator what
you changed and anything they need to know about it.
