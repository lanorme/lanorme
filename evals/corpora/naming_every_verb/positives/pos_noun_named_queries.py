"""Queries named for their value: the shapes the Clean Code reading renames."""


def shell_violations(tree):
    """Every shell-injection finding in *tree*."""
    return [node for node in tree.walk() if node.is_shell_call()]


def url_for(endpoint, **values):
    """The URL for *endpoint*."""
    return router.build(endpoint, values)


def _thresholds(settings):
    """The size thresholds in force."""
    return {"warn": settings.warn, "fail": settings.fail}


def proxy_headers(proxy):
    """The headers to send when tunnelling through *proxy*."""
    return {"Proxy-Authorization": proxy.auth} if proxy.auth else {}


def _basic_auth_str(username, password):
    """The Basic auth header value for the credentials."""
    return "Basic " + encode(f"{username}:{password}")


def dotenv_not_available():
    """Whether the dotenv package is missing; a predicate without its auxiliary."""
    return dotenv is None


class Session:
    """A session with two noun-named queries."""

    def response(self, request):
        """The response for *request*."""
        return self.transport.send(request)

    def cert_verify_result(self, conn):
        """The verification outcome for *conn*."""
        return conn.verify()
