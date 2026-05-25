"""BroadcastTracker — one in-flight broadcast per group."""
from __future__ import annotations

from aegis.groups.models import BroadcastRecord


class BroadcastInFlight(Exception):
    """Raised on a second `open` against a group with an open broadcast."""

    def __init__(self, group: str, open_id: str) -> None:
        super().__init__(f"group {group!r} already has open broadcast {open_id}")
        self.group = group
        self.open_id = open_id


class BroadcastTracker:
    def __init__(self) -> None:
        self._open: dict[str, BroadcastRecord] = {}

    def open(self, rec: BroadcastRecord) -> None:
        cur = self._open.get(rec.group)
        if cur is not None:
            raise BroadcastInFlight(rec.group, cur.id)
        self._open[rec.group] = rec

    def current(self, group: str) -> BroadcastRecord | None:
        return self._open.get(group)

    def close(self, group: str, broadcast_id: str) -> None:
        cur = self._open.get(group)
        if cur is not None and cur.id == broadcast_id:
            del self._open[group]
