"""Verbs that say what happens, and weak-verb names a framework dictates."""


def parse_data(data):
    """Turn the payload into a record."""
    return Record.from_payload(data)


def store_record(record):
    """Append the record to the store."""
    store.append(record)


def handle(event):
    """A bare ``handle`` is a dispatcher's slot, not a description."""
    event.dispatch()


def processed_items(items):
    """``processed`` is a participle, not the weak verb."""
    return [item for item in items if item.done]


class RequestHandler(BaseHTTPRequestHandler):
    """A subclass keeps its parent's protocol names."""

    def do_GET(self):
        self.send_response(200)

    def handle_error(self, request, client_address):
        self.log_error("bad request")


class Middleware:
    """Django middleware is a plain class with framework-named methods."""

    def process_request(self, request):
        request.started = True

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.view = view_func


class Pipeline:
    """A Scrapy pipeline, likewise."""

    def process_item(self, item, spider):
        return item


@app.errorhandler(404)
def handle_404(error):
    """A registered handler is named by the framework's convention."""
    return "not found", 404


def outer():
    """A closure named ``process`` is local."""

    def process(value):
        return value.strip()

    return process
