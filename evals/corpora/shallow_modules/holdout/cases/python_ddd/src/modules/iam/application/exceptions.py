from seedwork.application import ApplicationException


class InvalidCredentialsException(ApplicationException):
    def __init__(self, argument_1):
        return None


class NeutralClass1(ApplicationException):
    def __init__(self, argument_1):
        return None
