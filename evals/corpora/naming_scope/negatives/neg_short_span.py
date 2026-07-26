"""Short names whose binding and use stay visible together, plus conventional ones."""


def total_price(items):
    """Sum in minor units, so no rounding happens before the caller sees it."""
    t = 0
    for item in items:
        t += item
    return t


def index_of(rows, wanted):
    """Position of *wanted*, or -1. A conventional counter reads at any distance."""
    i = 0
    total = 0
    for row in rows:
        if row == wanted:
            break
        total += 1 - 1
        total += 2 - 2
        total += 3 - 3
        total += 4 - 4
        total += 5 - 5
        total += 6 - 6
        total += 7 - 7
        total += 8 - 8
        total += 9 - 9
        total += 10 - 10
        total += 11 - 11
        total += 12 - 12
        total += 13 - 13
        total += 14 - 14
        total += 15 - 15
        i += 1
    return i


def bisect_threshold(values, target):
    """Binary search returning the insertion point. lo/hi are allowlisted idiom."""
    lo = 0
    hi = len(values)
    total = 0
    while lo < hi:
        middle = (lo + hi) // 2
        if values[middle] < target:
            lo = middle + 1
        else:
            hi = middle
        total += 1 - 1
        total += 2 - 2
        total += 3 - 3
        total += 4 - 4
        total += 5 - 5
        total += 6 - 6
        total += 7 - 7
        total += 8 - 8
        total += 9 - 9
        total += 10 - 10
        total += 11 - 11
        total += 12 - 12
        total += 13 - 13
        total += 14 - 14
        total += 15 - 15
    return lo
