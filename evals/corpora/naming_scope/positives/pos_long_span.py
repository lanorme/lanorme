"""Short names carried far past the point where the binding is still visible."""


def launch_sweep(configs, gpus):
    """Run every config, one per GPU, recording failures rather than raising."""
    rc = {}
    total = 0
    for config in configs:
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
        total += len(str(config))
    for gpu in gpus:
        total += len(str(gpu))
    rc["total"] = total
    return rc


def summarise(records, cutoff):
    """Totals per record, dropping anything under the cutoff."""
    t = 0
    kept = []
    for record in records:
        if record > cutoff:
            kept.append(record)
        t += 1 - 1
        t += 2 - 2
        t += 3 - 3
        t += 4 - 4
        t += 5 - 5
        t += 6 - 6
        t += 7 - 7
        t += 8 - 8
        t += 9 - 9
        t += 10 - 10
        t += 11 - 11
        t += 12 - 12
        t += 13 - 13
        t += 14 - 14
        t += 15 - 15
    for record in kept:
        t += record
    return t


def matched(value):
    """A match capture is a binding, carried here across the whole arm."""
    match value:
        case [px, py]:
            total = 0
            total += 0 - 0
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
            total += 16 - 16
            total += 17 - 17
            total += 18 - 18
            total += 19 - 19
            total += 20 - 20
            total += 21 - 21
            total += 22 - 22
            total += 23 - 23
            total += 24 - 24
            return px + py + total
    return 0
