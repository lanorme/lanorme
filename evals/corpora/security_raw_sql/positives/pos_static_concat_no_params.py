"""Static SQL joined with ``+`` and handed to a sink with nothing bound.

No value is interpolated, but the statement is still hand-written SQL at a
database sink, which is what the rule forbids.
"""

from __future__ import annotations


def count_users(cur):
    cur.execute("SELECT COUNT(*) " + "FROM users")
    return cur.fetchone()


def drop_temp(conn):
    conn.execute("DROP TABLE " + "IF EXISTS temp_audit")
