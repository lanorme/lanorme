"""Noun-phrase classes whose first word happens to be a verb, plus message objects."""


class UserFetcher:
    """The thing that fetches users."""

    def fetch(self, directory):
        return directory.users()


class FetchOptions:
    """Options for a fetch: a noun head makes the compound a thing."""

    timeout = 30


class ConnectTimeout:
    """The timeout that applies while connecting."""

    seconds = 5


class CompileError(Exception):
    """A compile failed."""


class DeleteView:
    """A view that deletes; the head is the noun."""

    methods = ("DELETE",)


class SaveTest2:
    """A test case class; the numeric suffix does not change the head."""

    def test_save(self):
        assert True


class GetSetFactoryProtocol:
    """A protocol is a thing, however its first word reads."""


class CreateUserCommand:
    """A CQRS command object, marked by its suffix."""

    email = ""


class SendEmailJob:
    """A queued unit of work, marked by its suffix."""

    retries = 3


class ProcessPool:
    """A pool of processes: the first word is a noun modifier here."""

    size = 4


class ParseResult:
    """What a parse produced, as in urllib."""

    scheme = ""


class ResolveInfo:
    """Information about a resolution, as in GraphQL."""

    path = ()


class CreateUserResponse:
    """A response object, marked by its suffix."""

    id = 0


class Configurable:
    """A capability, named as an adjective."""


class CONSOLE_SCREEN_BUFFER_INFO:
    """A ctypes structure keeps the C name; not PascalCase, not judged."""
