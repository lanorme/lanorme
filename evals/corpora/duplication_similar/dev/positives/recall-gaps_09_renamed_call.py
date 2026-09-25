"""Two writers differing only by a renamed method call."""

from __future__ import annotations


def store_event(repo, event):
    record = repo.prepare(event)
    record.stamp()
    repo.save(record)
    repo.flush()
    return record.id


def store_alert(repo, alert):
    record = repo.prepare(alert)
    record.stamp()
    repo.persist(record)
    repo.flush()
    return record.id
