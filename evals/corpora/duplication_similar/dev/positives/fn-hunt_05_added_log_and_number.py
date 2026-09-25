from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def paginate_orders(items, page):
    size = 25
    start = page * size
    end = start + size
    window = items[start:end]
    has_more = end < len(items)
    return {"rows": window, "more": has_more}


def paginate_invoices(items, page):
    size = 50
    start = page * size
    end = start + size
    window = items[start:end]
    log.debug("paged invoices")
    has_more = end < len(items)
    return {"rows": window, "more": has_more}
