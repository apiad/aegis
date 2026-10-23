"""The library's result types, parsed from what the operations and channels
already send (a session's wire card, a transcript row). Unknown fields are
ignored, so a field aegis adds breaks no program; tests/test_library.py parses
each model from a real server's output, so a field renamed or removed fails CI.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class PlanItem(_Model):
    text: str
    state: Literal["pending", "doing", "done"]


class SessionInfo(_Model):
    """A session as its card shows it, at the moment ``Session.info()`` ran."""

    log_id: str
    handle: str
    title: str | None = None
    agent: str | None = None
    harness: str
    model: str | None = None
    effort: str | None = None
    permission: str | None = None
    cwd: str
    state: Literal["working", "idle", "asleep", "error"]
    # working, needs_you, review, done, error, ... (registry._attention).
    attention: str
    attention_line: str = ""
    replies: list[str] = Field(default_factory=list)
    plan: list[PlanItem] = Field(default_factory=list)
    cost_usd: float | None = None
    context_tokens: int | None = None
    created_at: float
    last_activity: float
    archived: bool = False


class Entry(_Model):
    """One transcript row, as the browser gets it."""

    id: str
    kind: str
    status: str
    ts: float | None = None
    title: str = ""
    summary: str = ""
    md: str | None = None
    detail: dict = Field(default_factory=dict)


class UserMessage(Entry):
    """A message to the agent."""


class Prose(Entry):
    """The agent's text; ``md`` holds it."""


class Thinking(Entry):
    """The agent's visible reasoning."""


class ToolCall(Entry):
    """A tool call, once it ended: ``status`` is ok, err or interrupted."""


class SystemNote(Entry):
    """A line from aegis or the harness: a spawn, a turn's cost, a restart."""


class ErrorNote(Entry):
    """Something failed: the harness exited, a message did not arrive."""


class OtherEntry(Entry):
    """A kind this version of the library does not model; ``kind`` names it."""


_KINDS: dict[str, type[Entry]] = {
    "user": UserMessage,
    "prose": Prose,
    "thinking": Thinking,
    "tool": ToolCall,
    "system": SystemNote,
    "error": ErrorNote,
}

# A row still changing: an event waits for it to settle.
UNSETTLED = frozenset({"running", "pending"})


AnyEntry = (
    UserMessage | Prose | Thinking | ToolCall | SystemNote | ErrorNote | OtherEntry
)


def entry(row: dict) -> AnyEntry:
    """The typed entry for a transcript row."""
    cls = _KINDS.get(row.get("kind", ""), OtherEntry)
    return cast(AnyEntry, cls.model_validate(row))


class Turn(_Model):
    """One turn, from the message that started it to its end."""

    attention: str | None
    line: str = ""
    replies: list[str] = Field(default_factory=list)
    text: str
    entries: list[AnyEntry]
    cost_usd: float | None = None


class TurnEnded(_Model):
    """The session stopped working; ``turn`` is the turn that ended."""

    turn: Turn


Event = AnyEntry | TurnEnded
