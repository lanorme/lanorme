"""Docstrings that only spell the signature back out."""


def calculate_total_price(items, tax_rate):
    """Calculate the total price."""
    subtotal = 0
    for item in items:
        subtotal += item
    taxed = subtotal * (1 + tax_rate)
    return taxed


def send_email(recipient, subject, body):
    """Sends an email."""
    envelope = {"to": recipient, "subject": subject}
    envelope["body"] = body
    envelope["retries"] = 0
    return envelope


def proc(d, m, t):
    """Process."""
    out = []
    for entry in d:
        out.append(entry * m)
    return out[:t]


def validate_user_input(payload):
    """Validate user input."""
    if not payload:
        return False
    if not isinstance(payload, dict):
        return False
    return "id" in payload
