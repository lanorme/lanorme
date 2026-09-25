"""Two senders differing by reordered setup lines and one kwarg name."""

from __future__ import annotations


def send_email(gateway, user, body):
    payload = {"body": body}
    recipient = user.email
    payload["to"] = recipient
    handle = gateway.dispatch(payload, channel="email")
    return handle.tracking_id


def send_sms(gateway, user, body):
    recipient = user.phone
    payload = {"body": body}
    payload["to"] = recipient
    handle = gateway.dispatch(payload, transport="email")
    return handle.tracking_id
