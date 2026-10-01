"""Importer stub: keeps only the imports that reach the labelled package."""
from searx.data import ENGINE_DESCRIPTIONS
import searx.answerers
from searx.metrics import get_engines_stats, get_engine_errors, get_reliabilities, histogram, counter, openmetrics


def neutral_deferred_imports():
    from searx.data import get_cache
    from searx.enginelib import ENGINES_CACHE
    return None
