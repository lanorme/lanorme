# swe_01_orders_report

Write `orders_report.py`.

We have a CSV export of orders with the columns `order_id`, `customer_id`,
`customer_email`, `country`, `placed_at` (ISO 8601), `status`, `currency`,
`amount_cents`, `refunded_cents`, `coupon_code`.

The module should:

- Load the CSV and drop rows that are malformed (bad dates, non-numeric
  amounts, blank customer id).
- Convert every amount to EUR using a rate table passed in by the caller.
- Compute, per customer: number of orders, net revenue (amount minus refunds),
  average order value, first and last order date, and whether they ever used a
  coupon.
- Compute, per country: net revenue, order count, and the top three customers
  by net revenue.
- Support filtering to a date window and to a set of statuses before the
  aggregation runs.
- Render the result as a plain-text report, sorted by net revenue descending,
  with a totals line at the bottom.

Standard library only. Include a `main()` with argument parsing.
