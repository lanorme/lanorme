"""Two string normalisers differing by a single added statement."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def normalise_tag(raw):
    text = raw.strip()
    text = text.lower()
    text = text.replace(" ", "-")
    text = text.strip("-")
    return text or "untitled"


def normalise_slug(raw):
    text = raw.strip()
    text = text.lower()
    text = text.replace(" ", "-")
    log.debug("slugifying %r", raw)
    text = text.strip("-")
    return text or "untitled"
