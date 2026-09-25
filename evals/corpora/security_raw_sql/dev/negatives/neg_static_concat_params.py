"""Static SQL pieces joined with ``+`` and bound through a params bag.

A literal joined to another literal, or to a module constant that is itself
a literal, is still static SQL: no value reaches the text. With placeholders
and a params payload the contract with the driver is intact.
"""

from __future__ import annotations

BASE = "SELECT id, email FROM users "


def user_by_id(cur, uid):
    cur.execute("SELECT id, email FROM users " + "WHERE id = %s", (uid,))
    return cur.fetchone()


def user_by_email(cur, email):
    cur.execute(BASE + "WHERE email = :email", {"email": email})
    return cur.fetchone()


def orders_since(cur, since):
    sql = "SELECT id, amount FROM orders " + "WHERE created_at >= ? " + "ORDER BY created_at"
    cur.execute(sql, (since,))
    return cur.fetchall()
