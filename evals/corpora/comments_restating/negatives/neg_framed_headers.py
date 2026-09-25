"""Single-line framed section headers: navigation aids, not restatement."""

from __future__ import annotations


def run(setup, total):
    # --- Setup ---
    setup()
    # === Totals ===
    total()
    # ## Teardown
    teardown = None
    return teardown
