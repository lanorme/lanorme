"""Queries that lead with a verb, predicates, constructors, conversions and protocol names."""


def find_shell_violations(tree):
    """Every shell-injection finding in *tree*."""
    return [node for node in tree.walk() if node.is_shell_call()]


def build_url(endpoint, **values):
    """The URL for *endpoint*."""
    return router.build(endpoint, values)


def line_has_noqa(line):
    """A subject-verb predicate reads as an assertion."""
    return "# noqa" in line


def is_valid(order):
    """A predicate with its auxiliary."""
    return bool(order.lines)


def matches_pattern(text, pattern):
    """Third-person verbs are verbs."""
    return pattern.search(text) is not None


def iter_blueprints(app):
    """An abbreviation of a verb is still a verb."""
    return iter(app.blueprints.values())


def getheaders(response):
    """A fused verb compound."""
    return response.headers


def dict_from_cookiejar(jar):
    """A conversion is named for its product."""
    return {cookie.name: cookie.value for cookie in jar}


def with_capacity(size):
    """A builder-style constructor."""
    return Buffer(size)


class Config:
    """Alternate constructors, a property and a protocol method are exempt."""

    @classmethod
    def of(cls, mapping):
        return cls(**mapping)

    @property
    def name(self):
        return self._name

    def keys(self):
        return list(self._values)

    @staticmethod
    def load(path):
        """A transparent decorator leaves the verb test in place."""
        return Config.of(read(path))


def layout(root):
    """A command is NAMING-007's, not this rule's."""
    (root / "domain.py").write_text("VALUE = 1\n")


def and_(*clauses):
    """A keyword clash."""
    return Conjunction(clauses)


def key_not_found(key):
    """A raiser exists to raise; it is neither command nor query."""
    raise KeyError(f"no such key: {key}")


def build_helper():
    """A closure is local; only the builder's own name is judged."""

    def helper():
        return 1

    return helper
