"""Two paginators differing only by a hard-coded page-size number."""

from __future__ import annotations


def page_users(users, page):
    start = page * 25
    end = start + 25
    window = users[start:end]
    has_next = end < len(users)
    return {"items": window, "page": page, "has_next": has_next}


def page_invoices(invoices, page):
    start = page * 50
    end = start + 50
    window = invoices[start:end]
    has_next = end < len(invoices)
    return {"items": window, "page": page, "has_next": has_next}
