"""Docstrings carrying something the signature cannot show."""


def calculate_total_price(items, tax_rate):
    """Total in minor units, so callers never see a rounded float."""
    subtotal = 0
    for item in items:
        subtotal += item
    return subtotal * (1 + tax_rate)


def send_email(recipient, subject, body):
    """Enqueue for the worker; delivery is not confirmed when this returns."""
    envelope = {"to": recipient, "subject": subject}
    envelope["body"] = body
    envelope["retries"] = 0
    return envelope


def validate_user_input(payload):
    """Structural validation only, the schema check happens downstream."""
    if not payload:
        return False
    if not isinstance(payload, dict):
        return False
    return "id" in payload


def retry_request(url, attempts):
    """Backs off exponentially with jitter, honouring Retry-After when present."""
    delay = 1
    for _ in range(attempts):
        delay *= 2
    return url, delay


def normalise_route(segments):
    """Collapse numeric and UUID path segments to :id so routes group sensibly."""
    parts = []
    for segment in segments:
        parts.append(segment.strip("/"))
    return "/".join(parts)


def aggregate_by_country(orders):
    """Revenue is net of refunds, converted at the rate table's EUR reference."""
    totals = {}
    for order in orders:
        totals[order] = totals.get(order, 0) + 1
    return totals
