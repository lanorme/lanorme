"""Importer stub: keeps only the imports that reach the labelled package."""
from confirmation import settings as confirmation_settings
from confirmation.models import (
    Confirmation,
    confirmation_url_for,
    create_confirmation_link,
    create_confirmation_object,
)

# Placeholders for names other case files import from this module.
do_create_multiuse_invite_link = None
do_get_invites_controlled_by_user = None
do_invite_users = None
do_revoke_multi_use_invite = None
do_revoke_user_invite = None
do_send_user_invite_email = None
