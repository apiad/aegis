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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from aegis.render_html import render_event_html
from aegis.state.event_codec import encode_event
from aegis.web.history import read_history

Sink = Callable[[dict], None]


@dataclass
class _HandleState:
    sinks: set = field(default_factory=set)
    seq: int = 0


class SubscriptionRegistry:
    def __init__(self, manager, state_dir: Path) -> None:
        self._m = manager
        self._state_dir = Path(state_dir)
        self._handles: dict[str, _HandleState] = {}

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

    def _attach(self, handle: str, hs: _HandleState) -> None:
        core = self._m.get(handle)
        if core is None:
            return
        if getattr(core, "_web_wired", False):
            return
        core._web_wired = True

        def on_event(c, ev):
            hs.seq += 1
            _fanout(hs, {
                "type": "stream", "kind": "event",
                "handle": handle, "seq": hs.seq,
                "event_type": type(ev).__name__,
                "event": encode_event(ev),
                "html": render_event_html(ev),
            })

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
