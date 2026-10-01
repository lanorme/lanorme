from lato import Command as LatoCommand
from pydantic import ConfigDict


class Command(LatoCommand):
    """Neutral description."""
    model_config = 0
