"""Docstrings whose words restate the name but which carry a why or a caveat."""


def parse_timestamp(raw):
    """Parse the timestamp. Do not use for pre-1970 dates, see issue #412."""
    cleaned = raw.strip()
    if not cleaned:
        return None
    return cleaned


def flush_buffer(buffer):
    """Flush the buffer, so that a crash cannot strand unwritten records."""
    written = 0
    for record in buffer:
        written += len(record)
    return written


def compute_checksum(payload):
    """Compute the checksum in bytes, not bits."""
    total = 0
    for byte in payload:
        total = (total + byte) % 65521
    return total


def rotate_shards(shards):
    """Rotate shards. TODO: make the rotation lazy once the ring lands."""
    rotated = list(shards)
    if rotated:
        rotated.append(rotated.pop(0))
    return rotated
