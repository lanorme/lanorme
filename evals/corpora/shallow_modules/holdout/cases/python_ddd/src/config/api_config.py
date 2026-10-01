from pydantic import Field
from pydantic_settings import BaseSettings


class ApiConfig(BaseSettings):
    """Neutral description.
    Neutral text.
    """
    neutral_attribute_1: object = 0
    neutral_attribute_2: object = 0
    neutral_attribute_3: object = 0
    neutral_attribute_4: object = [
        0,
    ]
    neutral_attribute_5: object = 0
