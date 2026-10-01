from pydantic import BaseModel


class BusinessRule(BaseModel):
    """Neutral description."""
    class Config:
        neutral_attribute_1 = 0
    _neutral_attribute_2: object = 0
    def neutral_method_1(self):
        return None
    def neutral_method_2(self):
        return None
    def __str__(self):
        return None
