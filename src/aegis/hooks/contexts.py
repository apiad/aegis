"""Typed event payloads + result for the hook substrate."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SessionHandle:
    """Read-only view of a session's identity for hook consumption."""
    handle:        str
    agent_profile: str
    harness:       str


@dataclass(frozen=True)
class Turn:
    """One historical turn surfaced to hooks."""
    role:    str   # "user" or "assistant"
    content: str


@dataclass(frozen=True)
class PreTurnResult:
    """Optional return from a pre_turn hook. All fields optional."""
    prepend_system: str | None = None
    rewrite_user:   str | None = None
    block:          str | None = None
    extend_history: tuple[Turn, ...] | None = None


@dataclass(frozen=True)
class PreTurnContext:
    """Payload for pre_turn hooks. Read-only."""
    session:       SessionHandle
    user_message:  str
    history:       tuple[Turn, ...]
    project_root:  Path
    prior_results: tuple[PreTurnResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PostTurnEvent:
    """Payload for post_turn observers."""
    session:           SessionHandle
    user_message:      str
    assistant_message: str
    project_root:      Path


@dataclass(frozen=True)
class SessionStartEvent:
    session:      SessionHandle
    project_root: Path


@dataclass(frozen=True)
class SessionEndEvent:
    session:      SessionHandle
    project_root: Path
    reason:       str
