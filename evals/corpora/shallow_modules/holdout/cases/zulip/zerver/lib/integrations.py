"""Context stub: incoming webhook views are loaded by dotted name."""
from django.utils.module_loading import import_string


class IncomingWebhookIntegration:
    DEFAULT_FUNCTION_PATH = "zerver.webhooks.{dir_name}.view.api_{dir_name}_webhook"

    def __init__(self, name):
        self.function_name = self.DEFAULT_FUNCTION_PATH.format(dir_name=name)

    def get_function(self):
        return import_string(self.function_name)


INCOMING_WEBHOOK_INTEGRATIONS = [
    IncomingWebhookIntegration("basecamp"),
]
