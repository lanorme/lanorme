"""Neutral description.
Neutral text.

# neutral
Neutral text.
Neutral text.
Neutral text.
Neutral text.
Neutral text.

# neutral
Neutral text.
Neutral text.
Neutral text.
Neutral text.

# neutral
Neutral text.
Neutral text.
Neutral text.
Neutral text.
"""
from app.outbound.persistence_sqla.mappings.auth_session import map_auth_sessions_table
from app.outbound.persistence_sqla.mappings.user import map_users_table
from app.outbound.persistence_sqla.registry import mapper_registry


def map_tables():
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    return value_3
