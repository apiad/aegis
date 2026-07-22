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

import asyncio
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from aegis.events import ThinkingTokens
from aegis.state.event_codec import encode_event
from aegis.web.compact import compact_encoded
from aegis.web.history import read_history

Sink = Callable[[dict], None]


def event_frame(handle: str, seq: int, ev) -> dict:
    """The canonical ``stream/event`` frame shape, shared by history replay
    (WSSession) and live fan-out (the per-handle observer). The ``event`` field
    is compacted (heavy bodies truncated); full detail is fetched on demand via
    the ``get_event`` RPC and rendered client-side."""
    compact, truncated = compact_encoded(encode_event(ev))
    return {
        "type": "stream", "kind": "event",
        "handle": handle, "seq": seq,
        "event_type": type(ev).__name__,
        "event": compact,
        "truncated": truncated,
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
        self._queue_subs: set[Sink] = set()
        self._digest = None
        self._indexer = None
        self._files_root: Path | None = None
        self._config_lock = asyncio.Lock()

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

    # -- queue digest stream ---------------------------------------------

    def set_digest(self, digest) -> None:
        self._digest = digest

    def subscribe_queue(self, sink: Sink) -> None:
        self._queue_subs.add(sink)

    def unsubscribe_queue(self, sink: Sink) -> None:
        self._queue_subs.discard(sink)

    def queue_digest_frame(self) -> dict:
        if self._digest is None:
            return {"type": "stream", "kind": "queue_digest",
                    "queues": [], "tasks": [], "last_started": None}
        snap = self._digest.snapshot()
        return {
            "type": "stream", "kind": "queue_digest",
            "queues": [asdict(q) for q in snap.queues],
            "tasks": [asdict(t) for t in snap.tasks],
            "last_started": (asdict(snap.last_started)
                             if snap.last_started else None),
        }

    def queue_tail(self, task_id: str) -> list[str]:
        if self._digest is None:
            return []
        return self._digest.tail_of(task_id)

    # -- file picker + viewer --------------------------------------------

    def set_files(self, indexer, root: Path) -> None:
        self._indexer = indexer
        self._files_root = Path(root)

    def file_search(self, query: str) -> list[str]:
        if self._indexer is None:
            return []
        q = (query or "").strip()
        return self._indexer.filter(q) if q else self._indexer.paths[:50]

    def file_read(self, path: str) -> dict:
        if self._files_root is None:
            return {"error": "files unavailable"}
        root = self._files_root
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return {"error": "path outside project"}
        if not target.is_file():
            return {"error": "not a file"}
        try:
            if target.stat().st_size > 2_000_000:
                return {"error": "file too large"}
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"error": str(exc)}
        ext = target.suffix.lower()
        kind = ("markdown" if ext in (".md", ".markdown")
                else "html" if ext in (".html", ".htm")
                else "source")
        return {"path": path, "kind": kind, "content": content}

    # -- config panel (read-only) ----------------------------------------

    def config_show(self) -> dict:
        """Read-only snapshot of agents / queues / schedules from
        .aegis.yaml. Mirrors the aegis_config_list_* MCP tools."""
        empty = {"agents": [], "queues": [], "schedules": []}
        from aegis.config import ConfigError, find_project_root
        from aegis.config.yaml_loader import load_config as _load_yaml
        root = self._files_root or find_project_root()
        if root is None:
            return empty
        try:
            cfg = _load_yaml(root)
        except ConfigError:
            return empty
        return {
            "agents": [
                {"slug": slug, "harness": a.harness, "model": a.model,
                 "effort": a.effort.value if a.effort else None,
                 "permission": a.permission.value}
                for slug, a in cfg.agents.items()
            ],
            "queues": [
                {"name": name, "agent": q.agent,
                 "max_parallel": q.max_parallel}
                for name, q in (cfg.queues or {}).items()
            ],
            "schedules": [
                {"name": name, "cron": s.get("cron"),
                 "enabled": s.get("enabled", True),
                 "workflow": s.get("workflow")}
                for name, s in (cfg.schedules or {}).items()
            ],
        }

    # -- config editing (writes .aegis.yaml, best-effort hot-register) ---

    def _config_root(self):
        from aegis.config import find_project_root
        return self._files_root or find_project_root()

    async def config_add_agent(self, slug, *, provider, model,
                               effort=None, permission=None) -> dict:
        from aegis.config import Agent, ConfigError
        from aegis.config.edit import add_agent as _add
        root = self._config_root()
        if root is None:
            return {"error": "no project root"}
        async with self._config_lock:
            try:
                _add(root, slug, provider=provider, model=model,
                     effort=effort, permission=permission)
            except ConfigError as e:
                return {"error": str(e)}
            try:
                kw = {"harness": provider, "model": model}
                if effort is not None:
                    kw["effort"] = effort
                if permission is not None:
                    kw["permission"] = permission
                if hasattr(self._m, "register_agent"):
                    self._m.register_agent(slug, Agent(**kw))
            except Exception as e:  # noqa: BLE001 — persisted; live is bonus
                return {"ok": True, "live": False, "note": str(e)}
        return {"ok": True, "live": True}

    async def config_remove_agent(self, slug: str) -> dict:
        from aegis.config import ConfigError
        from aegis.config.edit import remove_agent as _rm
        root = self._config_root()
        if root is None:
            return {"error": "no project root"}
        async with self._config_lock:
            try:
                _rm(root, slug)
            except ConfigError as e:
                return {"error": str(e)}
        return {"ok": True, "live": False}

    async def config_add_queue(self, name, *, agent, max_parallel) -> dict:
        from aegis.config import ConfigError, load_queues
        from aegis.config.edit import add_queue as _add
        root = self._config_root()
        if root is None:
            return {"error": "no project root"}
        async with self._config_lock:
            try:
                _add(root, name, agent=agent, max_parallel=int(max_parallel))
            except ConfigError as e:
                return {"error": str(e)}
            try:
                if hasattr(self._m, "register_queue"):
                    self._m.register_queue(load_queues(root)[name])
            except Exception as e:  # noqa: BLE001
                return {"ok": True, "live": False, "note": str(e)}
        return {"ok": True, "live": True}

    async def config_remove_queue(self, name: str) -> dict:
        from aegis.config import ConfigError
        from aegis.config.edit import remove_queue as _rm
        root = self._config_root()
        if root is None:
            return {"error": "no project root"}
        async with self._config_lock:
            try:
                _rm(root, name)
            except ConfigError as e:
                return {"error": str(e)}
        return {"ok": True, "live": False}

    # -- group dashboard (poll-on-open) ----------------------------------

    async def group_status(self) -> list[dict]:
        """Snapshot of every live group with members (incl. session state)
        and current broadcast. Best-effort — empty when no groups bridge."""
        groups = getattr(self._m, "groups", None)
        if groups is None:
            return []
        try:
            names = groups.runtime.registry.names()
        except Exception:
            return []
        states = {si.handle: si.state for si in self._m.list_sessions()}
        out: list[dict] = []
        for name in names:
            try:
                st = await groups.status(name)
            except Exception:
                continue
            for m in st.get("members", []):
                m["state"] = states.get(m["handle"], "")
            out.append(st)
        return out

    def broadcast_queue_digest(self) -> None:
        frame = self.queue_digest_frame()
        for sink in list(self._queue_subs):
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

    def get_event(self, handle: str, seq: int) -> dict:
        """Full (un-truncated) encoded event at ``seq`` for on-tap expansion.
        Reads the persisted JSONL — relies on W0 central persistence so live
        serve-mode events are on disk."""
        for s, ev in read_history(self._state_dir, handle):
            if s == seq:
                return {"event": encode_event(ev)}
        return {"event": None}

    def _attach(self, handle: str, hs: _HandleState) -> None:
        core = self._m.get(handle)
        if core is None:
            return
        if getattr(core, "_web_wired", False):
            return
        core._web_wired = True

        def on_event(c, ev):
            # ThinkingTokens are high-volume and not persisted to the JSONL,
            # so consuming a seq for them would drift the web stream from the
            # log line index that get_event resolves against (same rule as
            # inbox messages). The cumulative estimate still reaches the
            # client via the block's AssistantThinking + the state metrics.
            if isinstance(ev, ThinkingTokens):
                return
            hs.seq += 1
            _fanout(hs, event_frame(handle, hs.seq, ev))

        def on_state(c, state, finished):
            _fanout(hs, {
                "type": "stream", "kind": "state",
                "handle": handle, "state": state.value,
                "metrics": _metrics_str(c),
            })

        def on_inbox(c, msg):
            # Inbox messages are rendered but not persisted to the session log,
            # so they must NOT consume an event seq — otherwise every event
            # after a delivered message drifts one ahead of its JSONL line
            # index, which is what get_event resolves against.
            _fanout(hs, {
                "type": "stream", "kind": "inbox",
                "handle": handle,
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
