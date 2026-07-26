"""Docstrings that are empty, or filler with no content word left."""


def normalise_route(segments):
    """"""
    parts = []
    for segment in segments:
        parts.append(segment.strip("/"))
    return "/".join(parts)


def build_manifest(shards, counts):
    """Returns a value."""
    manifest = {"shards": shards}
    manifest["counts"] = counts
    manifest["version"] = 1
    return manifest


def aggregate_by_country(orders):
    """This is a helper function."""
    totals = {}
    for order in orders:
        totals[order] = totals.get(order, 0) + 1
    return totals


class Bucket:
    """Bucket."""

    def refill(self, elapsed):
        """Refill the bucket."""
        self.tokens = min(self.capacity, self.tokens + elapsed)
        self.stamp = elapsed
        self.dirty = True
        return self.tokens
