"""Functions that do something and return nothing, named as if they were things."""


def layout(root):
    """Write a two-file project under *root*."""
    (root / "domain.py").write_text("VALUE = 1\n")
    (root / "infra.py").write_text("class Repo:\n    pass\n")


def cert_verify(conn, url, verify):
    """Set the connection's certificate options for *url*."""
    conn.cert_reqs = "CERT_REQUIRED" if verify else "CERT_NONE"
    conn.ca_certs = verify if isinstance(verify, str) else None


def versioned_session(session):
    """Attach the version-bump listener to *session*."""
    session.listeners.append(bump_versions)


def history_mapper(local_mapper):
    """Configure a history table for *local_mapper* and register it."""
    table = local_mapper.local_table.to_history()
    local_mapper.history = table


class Console:
    """A terminal writer with two noun-named commands."""

    def bell(self):
        """Ring the terminal bell."""
        self.file.write("\a")

    def right_crop(self, amount):
        """Drop *amount* cells from the right edge in place."""
        self.cells = self.cells[:-amount]


def bump_versions(session):
    """Increase every dirty object's version counter."""
    for obj in session.dirty:
        obj.version += 1


def endpoint_register(app, endpoint):
    """Add *endpoint* to the app's table; ``end`` is not a fused verb head."""
    app.endpoints[endpoint.path] = endpoint


def password_reset(user):
    """Clear the stored hash; ``pass`` is not a fused verb head either."""
    user.password_hash = None


def user_count_update(stats):
    """Refresh the cached user count: the verb trails two nouns."""
    stats.users = stats.query.count()
