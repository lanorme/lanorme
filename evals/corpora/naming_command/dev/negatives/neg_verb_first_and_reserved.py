"""Commands named as commands, queries named for their value, and names a convention fixed."""

import pytest


def write_layout(root):
    """Write a two-file project under *root*."""
    (root / "domain.py").write_text("VALUE = 1\n")
    (root / "infra.py").write_text("class Repo:\n    pass\n")


def bulk_insert_rows(cursor, rows):
    """Insert every row in one round trip."""
    cursor.executemany("INSERT INTO t VALUES (?)", rows)


def re_apply_assignments(scope):
    """Apply the recorded assignments to *scope* again."""
    for name, value in scope.recorded:
        scope[name] = value


def _atomic_write(path, data):
    """Write *data* through a temporary file and rename it into place."""
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def mkdir_output(root):
    """A shell verb is a verb."""
    (root / "output").mkdir()


def rm_tree(root):
    """Likewise."""
    shutil.rmtree(root)


match sys.platform:
    case "win32":
        def write_registry(key, value):
            """A definition under a module-level match is still seen."""
            winreg.set(key, value)
    case _:
        def write_registry(key, value):
            """The fallback arm too."""
            config[key] = value


def thresholds():
    """The size thresholds, a query named for its value."""
    return {"warn": 300, "fail": 500}


def result_processor(dialect):
    """The value converter for *dialect*, or ``None`` when no conversion is needed."""
    return None


def rows(cursor):
    """Yield each row as it arrives."""
    yield from cursor


def key_not_found(key):
    """Raise the lookup error for *key* with a consistent message."""
    raise KeyError(f"no such key: {key}")


async def async_main():
    """Entry point for the asyncio program."""
    await run_forever()


def main():
    """Entry point."""
    run_forever()


def on_click(event):
    """Event hook, named for the moment it runs."""
    event.widget.pressed = True


def pytest_configure(config):
    """pytest hook."""
    config.addinivalue_line("markers", "slow")


@pytest.fixture
def db():
    """A fixture is named for what it provides."""
    store.clear()


@app.route("/")
def index():
    """A route handler is named by its endpoint."""
    app.hits.append(1)


class Editor:
    """A widget with a property setter and a protocol method."""

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    def keys(self):
        """Mapping protocol name, kept as is."""
        self._cache.clear()

    def setUp(self):
        """unittest protocol name; ``set`` is the verb."""
        self._cache = {}

    def _repr_mimebundle_(self, include, exclude):
        """IPython display protocol."""
        self._rendered = True


def to_dict(obj):
    """A conversion is named for its product."""
    obj.serialised = True


def and_(*clauses):
    """A trailing underscore marks a keyword clash."""
    clauses[0].combine(clauses[1:])


def outer():
    """Closures are local; their names are not judged."""

    def wrapper():
        registry.clear()

    return wrapper


def _dynamic_class_hook(ctx):
    """A plugin hook, named for its role."""
    ctx.api.defer()


class Repo(Protocol):
    """A stub body is a declaration, not a command."""

    def thresholds(self): ...


class Base(abc.ABC):
    """A dotted transparent decorator leaves the verb test in place."""

    @abc.abstractmethod
    def flush_all(self):
        self.buffer.clear()


class Echo(asyncio.Protocol):
    """asyncio protocol hooks keep the standard library's names."""

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.transport.write(data)


class Server(socketserver.TCPServer):
    """socketserver hook."""

    def server_bind(self):
        self.socket.setsockopt(1, 2, 1)


class Canvas(QWidget):
    """A camelCase method on a subclass is the base API's name (PEP 8's compatibility clause)."""

    def mousePressEvent(self, event):
        self.pressed = True


class Config(AppConfig):
    """A framework method hook, reserved on a method only."""

    def ready(self):
        import signals


def callback(result):
    """A bare role name handed to apply_async; the caller fixed it."""
    results.append(result)


async def app(scope, receive, send):
    """The ASGI callable, named by the specification's examples."""
    await send({"type": "http.response.start", "status": 200})


def refund_order(order):
    """A business verb is a verb."""
    order.refunded = True
