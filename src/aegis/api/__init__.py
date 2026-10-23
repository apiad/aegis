"""Typed access to an aegis server from another program (help page ``library``)."""

from .models import (
    AnyEntry,
    Entry,
    ErrorNote,
    Event,
    OtherEntry,
    PlanItem,
    Prose,
    SessionInfo,
    SystemNote,
    Thinking,
    ToolCall,
    Turn,
    TurnEnded,
    UserMessage,
)
from .sessions import Session, SessionNotFound, Sessions

__all__ = [
    "AnyEntry",
    "Entry",
    "ErrorNote",
    "Event",
    "OtherEntry",
    "PlanItem",
    "Prose",
    "Session",
    "SessionInfo",
    "SessionNotFound",
    "Sessions",
    "SystemNote",
    "Thinking",
    "ToolCall",
    "Turn",
    "TurnEnded",
    "UserMessage",
]
