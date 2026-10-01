"""Importer stub: keeps only the imports that reach the labelled package."""

NEUTRAL_REFERENCES = [
    'hc.integrations.email.transport.Email',
    'hc.integrations.github.transport.GitHub',
    'hc.integrations.googlechat.transport.GoogleChat',
    'hc.integrations.group.transport.Group',
    'hc.integrations.matrix.transport.Matrix',
    'hc.integrations.mattermost.transport.Mattermost',
    'hc.integrations.opsgenie.transport.Opsgenie',
    'hc.integrations.rocketchat.transport.RocketChat',
    'hc.integrations.shell.transport.Shell',
    'hc.integrations.slack.transport.Slack',
    'hc.integrations.telegram.transport.Telegram',
    'hc.integrations.trello.transport.Trello',
]

# Placeholders for names other case files import from this module.
Channel = None
Check = None
DEFAULT_GRACE = None
DEFAULT_TIMEOUT = None
Flip = None
Notification = None
Ping = None
TokenBucket = None
prepare_durations = None
