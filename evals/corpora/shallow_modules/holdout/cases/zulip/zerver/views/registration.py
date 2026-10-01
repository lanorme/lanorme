"""Importer stub: keeps only the imports that reach the labelled package."""
from confirmation import settings as confirmation_settings
from confirmation.models import (
    Confirmation,
    ConfirmationKeyError,
    create_confirmation_link,
    get_object_from_key,
    render_confirmation_key_error,
)

# Placeholders for names other case files import from this module.
accounts_register = None
create_demo_helper = None
prepare_realm_creation_url = None
