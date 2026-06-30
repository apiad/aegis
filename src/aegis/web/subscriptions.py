"""Per-handle observer fan-out for the web frontend.

One ``SubscriptionRegistry`` lives per ``aegis serve`` process. The first
``WSSession`` to subscribe to a handle causes the registry to attach a single
set of event/state/inbox observers to that ``AgentSession`` (guarded so a
second window does not double-attach); every subsequent subscriber just adds
its sink. Live events are turned into ``stream/*`` frames and pushed to every
sink. ``seq`` is the per-handle monotonic counter, initialised to the
persisted line count at attach time so it continues the JSONL line index.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from aegis.render_html import render_event_html
from aegis.state.event_codec import encode_event
from aegis.web.history import read_history

Sink = Callable[[dict], None]


def event_frame(handle: str, seq: int, ev) -> dict:
    """The canonical ``stream/event`` frame shape, shared by history replay
    (WSSession) and live fan-out (the per-handle observer)."""
    return {
        "type": "stream", "kind": "event",
        "handle": handle, "seq": seq,
        "event_type": type(ev).__name__,
        "event": encode_event(ev),
        "html": render_event_html(ev),
    }


@dataclass
class _HandleState:
    sinks: set = field(default_factory=set)
    seq: int = 0


class SubscriptionRegistry:
    def __init__(self, manager, state_dir: Path) -> None:
        self._m = manager
        self._state_dir = Path(state_dir)
        self._handles: dict[str, _HandleState] = {}
        self._globals: set[Sink] = set()

    # -- global session-list stream --------------------------------------

    def subscribe_global(self, sink: Sink) -> None:
        self._globals.add(sink)

    def unsubscribe_global(self, sink: Sink) -> None:
        self._globals.discard(sink)

    def session_list_frame(self) -> dict:
        return {
            "type": "stream", "kind": "session_list",
            "sessions": [asdict(si) for si in self._m.list_sessions()],
        }

    def broadcast_session_list(self) -> None:
        frame = self.session_list_frame()
        for sink in list(self._globals):
            sink(frame)

    async def subscribe(self, handle: str, sink: Sink) -> int:
        """Register ``sink`` for ``handle``; attach observers on first use.
        Returns the current persisted ``seq`` (history line count)."""
        hs = self._handles.get(handle)
        first = hs is None
        if hs is None:
            hs = _HandleState(seq=len(read_history(self._state_dir, handle)))
            self._handles[handle] = hs
        hs.sinks.add(sink)
        if first:
            self._attach(handle, hs)
        return hs.seq

    def unsubscribe(self, handle: str, sink: Sink) -> None:
        hs = self._handles.get(handle)
        if hs is not None:
            hs.sinks.discard(sink)

    def history(self, handle: str) -> list[tuple[int, "object"]]:
        """Persisted ``(seq, event)`` pairs for ``handle`` (subscribe/resume)."""
        return read_history(self._state_dir, handle)

    def _attach(self, handle: str, hs: _HandleState) -> None:
        core = self._m.get(handle)
        if core is None:
            return
        if getattr(core, "_web_wired", False):
            return
        core._web_wired = True

        def on_event(c, ev):
            hs.seq += 1
            _fanout(hs, event_frame(handle, hs.seq, ev))

        def on_state(c, state, finished):
            _fanout(hs, {
                "type": "stream", "kind": "state",
                "handle": handle, "state": state.value,
                "metrics": _metrics_str(c),
            })

        def on_inbox(c, msg):
            hs.seq += 1
            _fanout(hs, {
                "type": "stream", "kind": "inbox",
                "handle": handle, "seq": hs.seq,
                "msg": _inbox_dict(msg),
            })

        core.add_event_observer(on_event)
        core.add_state_observer(on_state)
        core.add_inbox_observer(on_inbox)


def _fanout(hs: _HandleState, frame: dict) -> None:
    for sink in list(hs.sinks):
        sink(frame)


def _metrics_str(core) -> str:
    try:
        return core.metrics.render(time.monotonic())
    except Exception:
        return ""


def _inbox_dict(msg) -> dict:
    return {
        "sender": msg.sender,
        "timestamp": msg.timestamp,
        "body": msg.body,
        "task_id": getattr(msg, "task_id", None),
        "status": getattr(msg, "status", None),
    }
