import contextlib


class InMemoryInbox:
    def __init__(self):
        return None
    def neutral_method_1(self):
        return None
    @contextlib.contextmanager
    def neutral_method_2(self):
        return None
    def neutral_method_3(self, argument_1):
        return None


class NeutralClass1:
    def __init__(self, argument_1):
        return None
    def neutral_method_4(self):
        return None


class InMemoryOutbox:
    def __init__(self):
        return None
    def neutral_method_5(self, argument_1):
        return None
