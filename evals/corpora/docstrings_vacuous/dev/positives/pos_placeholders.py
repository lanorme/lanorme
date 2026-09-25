"""Placeholder docstrings: a word that promises documentation is not documentation."""


def rotate_shards(shards):
    """TODO"""
    rotated = list(shards)
    if rotated:
        rotated.append(rotated.pop(0))
    return rotated


def flush_buffer(buffer):
    """Docstring."""
    written = 0
    for record in buffer:
        written += len(record)
    return written
