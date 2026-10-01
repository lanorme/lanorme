class DomainException(Exception):
    pass


class BusinessRuleValidationException(DomainException):
    def __init__(self, argument_1):
        return None
    def __str__(self):
        return None


class EntityNotFoundException(Exception):
    def __init__(self, argument_1, **keywords):
        value_3 = 0
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        return value_5
