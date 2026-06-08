"""Aegis hook substrate."""
from aegis.hooks.contexts import (
    PostTurnEvent, PreSpawnContext, PreSpawnResult,
    PreTurnContext, PreTurnResult,
    SessionEndEvent, SessionHandle, SessionStartEvent, Turn,
)
from aegis.hooks.decorator import _REGISTRY, hook, list_hooks

__all__ = [
    "PostTurnEvent", "PreSpawnContext", "PreSpawnResult",
    "PreTurnContext", "PreTurnResult",
    "SessionEndEvent", "SessionHandle", "SessionStartEvent", "Turn",
    "_REGISTRY", "hook", "list_hooks",
]
