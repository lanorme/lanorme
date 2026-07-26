"""Build a per-customer and per-country revenue report from an orders CSV.

Expected input columns:
    order_id, customer_id, customer_email, country, placed_at (ISO 8601),
    status, currency, amount_cents, refunded_cents, coupon_code

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

CENTS = Decimal(100)
TOP_N_CUSTOMERS = 3


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    """A single, already-validated and EUR-converted order."""

    order_id: str
    customer_id: str
    customer_email: str
    country: str
    placed_at: datetime
    status: str
    currency: str
    amount_cents: int
    refunded_cents: int
    coupon_code: str | None
    amount_eur: Decimal
    refunded_eur: Decimal

    @property
    def net_eur(self) -> Decimal:
        return self.amount_eur - self.refunded_eur

    @property
    def used_coupon(self) -> bool:
        return bool(self.coupon_code)


@dataclass
class CustomerSummary:
    customer_id: str
    order_count: int = 0
    gross_revenue: Decimal = Decimal("0")
    net_revenue: Decimal = Decimal("0")
    first_order: datetime | None = None
    last_order: datetime | None = None
    used_coupon: bool = False

    def add(self, order: Order) -> None:
        self.order_count += 1
        self.gross_revenue += order.amount_eur
        self.net_revenue += order.net_eur
        if self.first_order is None or order.placed_at < self.first_order:
            self.first_order = order.placed_at
        if self.last_order is None or order.placed_at > self.last_order:
            self.last_order = order.placed_at
        self.used_coupon = self.used_coupon or order.used_coupon

    @property
    def average_order_value(self) -> Decimal:
        if self.order_count == 0:
            return Decimal("0")
        return self.gross_revenue / self.order_count


@dataclass
class CountrySummary:
    country: str
    order_count: int = 0
    net_revenue: Decimal = Decimal("0")
    _customer_net: dict[str, Decimal] = field(default_factory=dict)

    def add(self, order: Order) -> None:
        self.order_count += 1
        self.net_revenue += order.net_eur
        self._customer_net[order.customer_id] = (
            self._customer_net.get(order.customer_id, Decimal("0")) + order.net_eur
        )

    def top_customers(self, n: int = TOP_N_CUSTOMERS) -> list[tuple[str, Decimal]]:
        ranked = sorted(self._customer_net.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:n]


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_eur(cents: int, currency: str, rates: Mapping[str, float]) -> Decimal | None:
    currency = currency.strip().upper()
    if currency == "EUR":
        rate = Decimal("1")
    elif currency in rates:
        rate = Decimal(str(rates[currency]))
    else:
        return None
    try:
        return (Decimal(cents) / CENTS) * rate
    except InvalidOperation:
        return None


def load_orders(path: str, rates: Mapping[str, float]) -> list[Order]:
    """Load `path`, dropping malformed rows, and convert amounts to EUR.

    A row is malformed (and silently dropped) if it has a blank customer id,
    an unparseable ``placed_at`` date, a non-numeric ``amount_cents`` or
    ``refunded_cents``, or a currency missing from `rates`. A blank
    ``refunded_cents`` is treated as zero rather than malformed.
    """
    orders: list[Order] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            order = _row_to_order(row, rates)
            if order is not None:
                orders.append(order)
    return orders


def _row_to_order(row: Mapping[str, str], rates: Mapping[str, float]) -> Order | None:
    customer_id = (row.get("customer_id") or "").strip()
    if not customer_id:
        return None

    placed_at = _parse_date(row.get("placed_at") or "")
    if placed_at is None:
        return None

    amount_cents = _parse_int(row.get("amount_cents") or "")
    if amount_cents is None:
        return None

    refunded_raw = (row.get("refunded_cents") or "").strip()
    if refunded_raw == "":
        refunded_cents = 0
    else:
        refunded_cents = _parse_int(refunded_raw)
        if refunded_cents is None:
            return None

    currency = (row.get("currency") or "").strip()
    amount_eur = _to_eur(amount_cents, currency, rates)
    refunded_eur = _to_eur(refunded_cents, currency, rates)
    if amount_eur is None or refunded_eur is None:
        return None

    coupon_code = (row.get("coupon_code") or "").strip() or None

    return Order(
        order_id=(row.get("order_id") or "").strip(),
        customer_id=customer_id,
        customer_email=(row.get("customer_email") or "").strip(),
        country=(row.get("country") or "").strip(),
        placed_at=placed_at,
        status=(row.get("status") or "").strip(),
        currency=currency.upper(),
        amount_cents=amount_cents,
        refunded_cents=refunded_cents,
        coupon_code=coupon_code,
        amount_eur=amount_eur,
        refunded_eur=refunded_eur,
    )


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------


def filter_orders(
    orders: Sequence[Order],
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    statuses: Sequence[str] | None = None,
) -> list[Order]:
    """Filter `orders` to a date window (inclusive) and/or a set of statuses."""
    start_dt = _as_datetime(start, end_of_day=False)
    end_dt = _as_datetime(end, end_of_day=True)
    allowed_statuses = {status.strip().lower() for status in statuses} if statuses else None

    result = []
    for order in orders:
        if start_dt is not None and order.placed_at < start_dt:
            continue
        if end_dt is not None and order.placed_at > end_dt:
            continue
        if allowed_statuses is not None and order.status.lower() not in allowed_statuses:
            continue
        result.append(order)
    return result


def _as_datetime(value: date | datetime | None, *, end_of_day: bool) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    time_part = datetime.max.time() if end_of_day else datetime.min.time()
    return datetime.combine(value, time_part)


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def aggregate_by_customer(orders: Sequence[Order]) -> dict[str, CustomerSummary]:
    summaries: dict[str, CustomerSummary] = {}
    for order in orders:
        summary = summaries.setdefault(order.customer_id, CustomerSummary(order.customer_id))
        summary.add(order)
    return summaries


def aggregate_by_country(orders: Sequence[Order]) -> dict[str, CountrySummary]:
    summaries: dict[str, CountrySummary] = {}
    for order in orders:
        summary = summaries.setdefault(order.country, CountrySummary(order.country))
        summary.add(order)
    return summaries


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f} EUR"


def _date(value: datetime | None) -> str:
    return value.date().isoformat() if value is not None else "-"


def render_report(
    customers: Mapping[str, CustomerSummary],
    countries: Mapping[str, CountrySummary],
) -> str:
    """Render a plain-text report, sorted by net revenue descending."""
    lines: list[str] = []
    lines.append("ORDERS REPORT")
    lines.append("=============")
    lines.append("")

    lines.extend(_render_customer_section(customers))
    lines.append("")
    lines.extend(_render_country_section(countries))

    return "\n".join(lines) + "\n"


def _render_customer_section(customers: Mapping[str, CustomerSummary]) -> list[str]:
    lines = ["By customer (net revenue descending)", "-" * 38]
    ranked = sorted(customers.values(), key=lambda c: c.net_revenue, reverse=True)

    total_orders = 0
    total_net = Decimal("0")
    for summary in ranked:
        coupon = "yes" if summary.used_coupon else "no"
        lines.append(
            f"{summary.customer_id:<20} orders={summary.order_count:<5} "
            f"net={_money(summary.net_revenue):>16} "
            f"avg={_money(summary.average_order_value):>16} "
            f"first={_date(summary.first_order)} last={_date(summary.last_order)} "
            f"coupon={coupon}"
        )
        total_orders += summary.order_count
        total_net += summary.net_revenue

    lines.append("-" * 38)
    lines.append(f"TOTAL customers={len(ranked)} orders={total_orders} net={_money(total_net)}")
    return lines


def _render_country_section(countries: Mapping[str, CountrySummary]) -> list[str]:
    lines = ["By country (net revenue descending)", "-" * 38]
    ranked = sorted(countries.values(), key=lambda c: c.net_revenue, reverse=True)

    total_orders = 0
    total_net = Decimal("0")
    for summary in ranked:
        top = ", ".join(
            f"{customer_id} ({_money(net)})" for customer_id, net in summary.top_customers()
        )
        lines.append(
            f"{summary.country or '(unknown)':<20} orders={summary.order_count:<5} "
            f"net={_money(summary.net_revenue):>16} top_customers=[{top}]"
        )
        total_orders += summary.order_count
        total_net += summary.net_revenue

    lines.append("-" * 38)
    lines.append(f"TOTAL countries={len(ranked)} orders={total_orders} net={_money(total_net)}")
    return lines


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_rates(path: str) -> dict[str, float]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"rates file {path!r} must contain a JSON object of currency -> rate")
    return {str(currency).upper(): float(rate) for currency, rate in data.items()}


def _parse_date_arg(value: str) -> date:
    return date.fromisoformat(value)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarise an orders CSV export by customer and by country.",
    )
    parser.add_argument("csv_path", help="path to the orders CSV export")
    parser.add_argument(
        "--rates",
        required=True,
        help="path to a JSON file mapping currency code to its rate to EUR",
    )
    parser.add_argument(
        "--start",
        type=_parse_date_arg,
        default=None,
        help="only include orders placed on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=_parse_date_arg,
        default=None,
        help="only include orders placed on or before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--status",
        action="append",
        dest="statuses",
        default=None,
        help="only include orders with this status; may be repeated",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="write the report to this file instead of stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    rates = _load_rates(args.rates)
    orders = load_orders(args.csv_path, rates)
    orders = filter_orders(orders, start=args.start, end=args.end, statuses=args.statuses)

    customers = aggregate_by_customer(orders)
    countries = aggregate_by_country(orders)
    report = render_report(customers, countries)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(report)
    else:
        sys.stdout.write(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
