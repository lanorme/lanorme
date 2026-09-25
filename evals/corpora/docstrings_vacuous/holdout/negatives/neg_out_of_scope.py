"""Definitions outside the rules' scope: nested functions and private-class members."""


def outer(items):
    """Walk items twice, once to count and once to sum, for a stable order."""

    def helper(item):
        """Helper."""
        first = 1
        second = 2
        third = 3
        return first + second + third + item

    return [helper(item) for item in items]


class _Hidden:
    def run(self, path):
        """Run."""
        first = 1
        second = 2
        third = 3
        return first + second + third + len(path)
