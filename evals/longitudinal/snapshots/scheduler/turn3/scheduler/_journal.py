"""A durable, append-only event log, used both to persist a run for later
inspection and to resume one that was killed mid-flight.

Events are written one JSON object per line (JSON Lines). Each write is
flushed and ``fsync``'d before :meth:`Journal.append` returns, so a process
killed at any point -- between two tasks, or mid-attempt -- leaves behind a
file whose every complete line really happened, with nothing buffered only
in memory to be lost. Reading a journal back (:meth:`Journal.read_events`)
tolerates a truncated final line (the one that might have been mid-write
when the process died) by dropping it rather than raising.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Protocol

from ._events import Event


class JournalLike(Protocol):
    """What :class:`~scheduler._scheduler.Run` needs from a journal.

    Satisfied by :class:`Journal` and by the no-op journal used when a run
    is not being persisted.
    """

    def append(self, event: Event) -> None: ...

    def close(self) -> None: ...


class Journal:
    """An open, append-only handle onto a run's event log file."""

    def __init__(self, path: str | Path, *, truncate: bool = False) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._file = self._path.open("w" if truncate else "a", encoding="utf-8")

    def append(self, event: Event) -> None:
        """Write ``event`` and make sure it is durable on disk before
        returning."""
        line = json.dumps(event.to_dict(), sort_keys=True)
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @staticmethod
    def read_events(path: str | Path) -> list[Event]:
        """Replay a journal file into the list of events it holds, in the
        order they were written.

        A path that does not exist yet reads as empty -- a run that has
        never been persisted before. A malformed trailing line (the process
        died mid-write, before a full line landed) is dropped rather than
        raised; a malformed line anywhere else is a real corruption and is
        raised as a :class:`ValueError`.
        """
        file_path = Path(path)
        if not file_path.exists():
            return []

        lines = file_path.read_text(encoding="utf-8").splitlines()
        events: list[Event] = []
        last_index = len(lines) - 1
        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                if index == last_index:
                    break  # a partial last line from a mid-write kill
                raise ValueError(
                    f"corrupt journal {file_path}: unreadable line {index + 1}"
                ) from exc
            events.append(Event.from_dict(data))
        return events


class _NullJournal:
    """The journal used when a run has no ``journal_path``: same interface,
    every call a no-op, so :class:`~scheduler._scheduler.Run` never needs to
    branch on whether persistence is enabled."""

    def append(self, event: Event) -> None:
        pass

    def close(self) -> None:
        pass


NULL_JOURNAL: JournalLike = _NullJournal()
