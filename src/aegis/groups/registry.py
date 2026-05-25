"""GroupRegistry — in-memory CRUD for groups + members."""
from __future__ import annotations

from typing import Any

from aegis.groups.models import Group, MemberRef
from aegis.groups.persistence import (
    event_broadcast_completed,
    event_created,
    event_dissolved,
    event_member_added,
    event_member_removed,
    event_renamed,
)
from aegis.queue.schema import now_iso


class GroupExists(Exception):
    """Raised when create/rename collides with an existing live group."""


class UnknownGroup(Exception):
    """Raised when an operation targets a group that doesn't exist."""


class GroupRegistry:
    def __init__(self, log=None) -> None:
        self._groups: dict[str, Group] = {}
        self._log = log

    def _emit(self, group: str, rec: dict[str, Any]) -> None:
        if self._log is not None:
            self._log.write(group, rec)

    def create(self, name: str, *, sender: str = "system",
               at: str = "") -> Group:
        if name in self._groups:
            raise GroupExists(name)
        g = Group(name=name)
        self._groups[name] = g
        self._emit(name, event_created(name, sender, at or now_iso()))
        return g

    def get(self, name: str) -> Group:
        if name not in self._groups:
            raise UnknownGroup(name)
        return self._groups[name]

    def names(self) -> list[str]:
        return sorted(self._groups)

    def add_member(self, group: str, ref: MemberRef, *,
                   sender: str = "system", at: str = "") -> None:
        g = self._groups.get(group)
        if g is None:
            g = self.create(group, sender=sender, at=at)
        g.members[ref.handle] = ref
        self._emit(group, event_member_added(
            ref.handle, ref.profile, sender, at or now_iso()))

    def remove_member(self, group: str, handle: str, *,
                      reason: str = "closed-by-user",
                      at: str = "") -> None:
        g = self.get(group)
        if handle in g.members:
            g.members.pop(handle)
            self._emit(group, event_member_removed(
                handle, reason, at or now_iso()))
        if not g.members:
            self._groups.pop(group, None)
            self._emit(group, event_dissolved("empty", at or now_iso()))

    def move_member(self, handle: str, *, from_group: str,
                    to_group: str) -> None:
        ref = self.get(from_group).members[handle]
        self.add_member(to_group, ref)
        self.remove_member(from_group, handle)

    def dissolve(self, group: str, *, reason: str = "dissolved",
                 at: str = "") -> None:
        if group not in self._groups:
            raise UnknownGroup(group)
        del self._groups[group]
        self._emit(group, event_dissolved(reason, at or now_iso()))

    def start(self, *, live_handles: set[str]) -> None:
        if self._log is None:
            return
        for group in self._log.all_groups():
            records = self._log.read(group)
            members: dict[str, MemberRef] = {}
            for rec in records:
                k = rec["kind"]
                if k == "member_added":
                    members[rec["handle"]] = MemberRef(
                        handle=rec["handle"], profile=rec["profile"])
                elif k == "member_removed":
                    members.pop(rec["handle"], None)
            in_flight = self._in_flight_broadcasts(records)
            self._groups[group] = Group(name=group, members=members)
            for handle in list(members):
                if handle not in live_handles:
                    members.pop(handle)
                    self._emit(group, event_member_removed(
                        handle, "lost-on-restart", now_iso()))
            if not members:
                self._groups.pop(group, None)
                self._emit(group, event_dissolved(
                    "empty-on-restart", now_iso()))
            for bid in in_flight:
                self._emit(group, event_broadcast_completed(
                    bid, "failed:interrupted", "concat", now_iso()))

    @staticmethod
    def _in_flight_broadcasts(records: list[dict]) -> list[str]:
        started = {r["broadcast_id"] for r in records
                   if r["kind"] == "broadcast_started"}
        completed = {r["broadcast_id"] for r in records
                     if r["kind"] == "broadcast_completed"}
        return sorted(started - completed)

    def rename(self, old: str, new: str, *, at: str = "") -> None:
        if new in self._groups:
            raise GroupExists(new)
        g = self.get(old)
        renamed = Group(name=new, members=dict(g.members))
        self._groups[new] = renamed
        del self._groups[old]
        self._emit(old, event_renamed(old, new, at or now_iso()))
