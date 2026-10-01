"""Importer stub: keeps only the imports that reach the labelled package."""
from confirmation import settings as confirmation_settings


def neutral_deferred_imports():
    from confirmation.models import Confirmation
    return None

NEUTRAL_REFERENCES = [
    'confirmation.Confirmation',
]

# Placeholders for names other case files import from this module.
EmailChangeStatus = None
MultiuseInvite = None
PreregistrationRealm = None
PreregistrationUser = None
RealmCreationStatus = None
RealmReactivationStatus = None
filter_to_valid_prereg_users = None
