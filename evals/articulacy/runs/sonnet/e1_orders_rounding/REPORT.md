# orders_report.py changes

## What changed

1. Every monetary figure is rounded to two decimal places.
   `_money()` already quantized on display, but the underlying values were
   full-precision `Decimal`s that got quantized independently at each print
   site. That is fine for a single number but breaks sums (see next point).

2. Net revenue figures now reconcile exactly.
   Added `apportion()`, which rounds a set of values to the cent using the
   largest-remainder method: round every value down to the cent, then hand
   the leftover (or excess) cent to the entries whose exact value was
   furthest from its naive rounding, until the rounded parts sum to the
   rounded total. This replaces rounding each number independently, which
   can drift by a cent or more once several rounded figures are added back
   up.

   It is applied at three levels, each pinned to the total above it:
   - Per-customer net revenue (`By customer` section) sums exactly to that
     section's `TOTAL ... net=` line.
   - Per-country net revenue (`By country` section) sums exactly to that
     section's `TOTAL ... net=` line.
   - Within each country, every customer's net revenue (not just the top 3
     shown) is apportioned against that country's already-rounded net
     revenue figure, via the new `CountrySummary.customer_net_totals()`
     accessor. The three customers shown in `top_customers=[...]` are drawn
     from this reconciled breakdown, so if a country has three or fewer
     customers the printed figures add up exactly to the country total; with
     more than three, the top-3 list is still a subset by design (it always
     was), but the underlying numbers are internally consistent.

   Average order value is still just rounded on its own; it is not a sum of
   displayed figures, so there is nothing to reconcile there.

3. Added `--currency`.
   Reuses the existing `--rates` table (currency to EUR rate) to convert in
   the other direction: EUR amounts are converted to the requested currency
   by dividing by that currency's rate before rounding. `--currency EUR`
   (the default) is a no-op. Rounding and reconciliation happen after
   conversion, so the report is internally consistent in whatever currency
   you ask for, not just in EUR.

   Requesting a currency that is not `EUR` and is not in the rates file (or
   whose rate is `0`) is a usage error: the CLI prints a message and exits
   with status 2, the same as any other bad argument.

## Operator notes

- No existing CLI flags changed meaning; `--currency` is new and optional,
  defaulting to `EUR`, so existing invocations are unaffected.
- The report's numbers may shift slightly from before, by a cent, at the
  entries where the largest-remainder method picks a different rounding
  than plain per-value rounding did. This is expected and is the fix; the
  new numbers are the ones that reconcile.
- `top_customers=[...]` remains a top-3 view, same as before; it is not a
  full per-country customer breakdown. Do not expect it to sum to the
  country total when a country has more than three customers.
