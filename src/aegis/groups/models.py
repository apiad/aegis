"""Group, member, broadcast, and result records."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MemberStatus = Literal["done", "canceled", "errored", "timeout", "lost"]


@dataclass(frozen=True)
class MemberRef:
    handle: str
    profile: str


@dataclass(frozen=True)
class MemberResult:
    handle: str
    text: str
    turn_ms: int
    tokens_in: int
    tokens_out: int
    status: MemberStatus


@dataclass(frozen=True)
class GroupResult:
    broadcast_id: str
    by_member: dict[str, MemberResult]
    combined: Any
    errors: dict[str, str]
    timeouts: list[str]


@dataclass
class Group:
    """A live group. Mutable: members come and go through the registry."""
    name: str
    members: dict[str, MemberRef] = field(default_factory=dict)


@dataclass(frozen=True)
class BroadcastRecord:
    id: str
    group: str
    sender: str
    objective: str
    output_format: str
    tool_guidance: str
    boundaries: str
    started_at: str
    members: tuple[str, ...]
