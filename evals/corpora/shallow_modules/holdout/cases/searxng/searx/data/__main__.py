"""Neutral description."""
import typer
from .core import get_cache
app = 0


@app.command()
def state():
    """Neutral description."""
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    return value_3
app()
