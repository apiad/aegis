"""GroupRegistry — in-memory CRUD for groups + members."""
from __future__ import annotations

from aegis.groups.models import Group, MemberRef


class GroupExists(Exception):
    """Raised when create/rename collides with an existing live group."""


class UnknownGroup(Exception):
    """Raised when an operation targets a group that doesn't exist."""


class GroupRegistry:
    def __init__(self) -> None:
        self._groups: dict[str, Group] = {}

    def create(self, name: str) -> Group:
        if name in self._groups:
            raise GroupExists(name)
        g = Group(name=name)
        self._groups[name] = g
        return g

    def get(self, name: str) -> Group:
        if name not in self._groups:
            raise UnknownGroup(name)
        return self._groups[name]

    def names(self) -> list[str]:
        return sorted(self._groups)

    def add_member(self, group: str, ref: MemberRef) -> None:
        g = self._groups.get(group)
        if g is None:
            g = self.create(group)
        g.members[ref.handle] = ref

    def remove_member(self, group: str, handle: str) -> None:
        g = self.get(group)
        g.members.pop(handle, None)
        if not g.members:
            self._groups.pop(group, None)

    def move_member(self, handle: str, *, from_group: str,
                    to_group: str) -> None:
        ref = self.get(from_group).members[handle]
        self.add_member(to_group, ref)
        self.remove_member(from_group, handle)

    def dissolve(self, group: str) -> None:
        if group not in self._groups:
            raise UnknownGroup(group)
        del self._groups[group]

    def rename(self, old: str, new: str) -> None:
        if new in self._groups:
            raise GroupExists(new)
        g = self.get(old)
        renamed = Group(name=new, members=dict(g.members))
        self._groups[new] = renamed
        del self._groups[old]
