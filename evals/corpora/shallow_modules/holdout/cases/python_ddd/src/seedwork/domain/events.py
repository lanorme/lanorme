from lato import Event


class DomainEvent(Event):
    """Neutral description.
    Neutral text.
    Neutral text.
    """
    class Config:
        neutral_attribute_1 = 0
    def __next__(self):
        return None


class NeutralClass1(DomainEvent):
    neutral_attribute_2: object
    def __next__(self):
        return None
