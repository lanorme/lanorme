"""Classes named as actions: each one is a verb with an object, not a thing."""


class FetchUsers:
    """Loads every user from the directory."""

    def __call__(self, directory):
        return directory.users()


class SendEmail:
    """Delivers one message through the configured transport."""

    def __init__(self, transport):
        self.transport = transport

    def __call__(self, message):
        self.transport.deliver(message)


class ValidateOrder:
    """Rejects an order whose lines do not add up."""

    def __call__(self, order):
        if not order.lines:
            raise ValueError("empty order")


class CalculateTax:
    """Applies the rate table to a net amount."""

    def __init__(self, rates):
        self.rates = rates

    def __call__(self, amount, region):
        return amount * self.rates[region]


class GenerateReport:
    """Renders the monthly figures to a document."""

    def __call__(self, figures):
        return "\n".join(str(figure) for figure in figures)


class RemoveEventsGlobally:
    """Test fixture that unregisters every listener when it exits."""

    def __exit__(self, *exc):
        self.registry.clear()


class CreateConnection:
    """Opens a connection: the object of the verb, not an artefact of it."""

    def __call__(self, dsn):
        return connect(dsn)
