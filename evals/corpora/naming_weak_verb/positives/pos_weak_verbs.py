"""Functions whose verb says something happens without saying what."""


def handle_data(data):
    """Parse the payload and store it."""
    record = parse(data)
    store.append(record)


def process_order(order):
    """Validate, price and persist an order."""
    order.validate()
    order.total = price(order)
    repository.save(order)


def perform_cleanup(root):
    """Delete the temporary files under *root*."""
    for path in root.glob("*.tmp"):
        path.unlink()


def do_something_with_mapped_class(cls):
    """Register the class with the mapper."""
    registry.register(cls)


def manage_users(users):
    """Deactivate every user past the retention date."""
    for user in users:
        if user.expired:
            user.deactivate()


def deal_with_error(error):
    """Log the error and raise it again."""
    log.error(error)
    raise error


class Report:
    """A plain class whose method is the author's to name."""

    def process_rows(self, rows):
        """Sum the rows into the totals."""
        for row in rows:
            self.total += row.amount
