"""The WebSocket protocol handler — one ``WSSession`` per connected browser
window. Runs the contract fixed in
``docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`` over an
abstract transport (Starlette's ``WebSocket`` satisfies ``WSTransport``
directly; tests use an in-memory fake).

Outbound frames flow through a single bounded queue drained by a sender task,
so live observer frames (which arrive on sync callbacks and cannot await) and
request responses share one FIFO ordering. Overflowing the queue closes the
socket with reason ``backpressure`` — the client reconnects and resumes from
JSONL, which is the durable source of truth.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from typing import Protocol

from aegis.events import AssistantText, AssistantThinking
from aegis.queue import InboxMessage, now_iso, sender_user
from aegis.web.subscriptions import SubscriptionRegistry, event_frame

PROTOCOL_VERSION = 2


def _tail_lower_seq(history: list[tuple[int, object]], tail: int) -> int:
    """Exclusive lower-bound seq so that streaming events with ``seq >
    result`` yields the last ``tail`` *coalesced* blocks. Returns 0 ("send
    everything") when there are ``tail`` or fewer blocks or tail<=0.

    Mirrors ``render.coalesce_chunks``' grouping — adjacent AssistantText /
    AssistantThinking sharing ``(type, message_id)`` are one block; any other
    event always starts a new block — so the cut never lands mid-message.
    """
    if tail <= 0:
        return 0
    starts: list[int] = []
    buf_type: type | None = None
    buf_mid: object = None
    have_buf = False
    for seq, ev in history:
        if isinstance(ev, (AssistantText, AssistantThinking)):
            mid = getattr(ev, "message_id", None)
            if have_buf and buf_type is type(ev) and buf_mid == mid:
                continue
            buf_type, buf_mid, have_buf = type(ev), mid, True
        else:
            buf_type, buf_mid, have_buf = None, None, False
        starts.append(seq)
    if len(starts) <= tail:
        return 0
    return starts[-tail] - 1
AUTH_TIMEOUT_S = 5.0
DEFAULT_SEND_CAP = 10_000
SUPPORTED_KINDS = [
    "event", "state", "inbox", "session_list", "queue_digest",
    "history_complete", "window_reset",
]


class WSDisconnect(Exception):
    """Raised by a transport's ``receive_json`` when the peer disconnects."""


class WSTransport(Protocol):
    async def send_json(self, obj: dict) -> None: ...
    async def receive_json(self) -> dict: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class WSSession:
    def __init__(self, transport: WSTransport, manager,
                 registry: SubscriptionRegistry, web_cfg, constants: dict,
                 *, server_version: str = "0",
                 send_cap: int = DEFAULT_SEND_CAP) -> None:
        self._t = transport
        self._m = manager
        self._reg = registry
        self._token = web_cfg.token
        self._constants = constants
        self._server_version = server_version
        self._out: asyncio.Queue = asyncio.Queue(maxsize=send_cap)
        self._overflow = asyncio.Event()
        self._subs: dict[str, dict] = {}   # handle -> {sink, buffering, buffer}
        self._global_sink = lambda fr: self._emit(fr)
        self._global_on = False
        self._queue_sink = lambda fr: self._emit(fr)
        self._queue_on = False

    # -- lifecycle --------------------------------------------------------

    async def run(self) -> None:
        if not await self._authenticate():
            return
        self._emit(self._hello())
        sender = asyncio.ensure_future(self._sender())
        watcher = asyncio.ensure_future(self._watch_overflow())
        reader = asyncio.ensure_future(self._read_loop())
        try:
            await asyncio.wait({reader, watcher},
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, watcher, sender):
                task.cancel()
            for task in (reader, watcher, sender):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if not self._overflow.is_set():
                await self._drain_remaining()
            self._detach_all()
            if self._global_on:
                self._reg.unsubscribe_global(self._global_sink)
            if self._queue_on:
                self._reg.unsubscribe_queue(self._queue_sink)

    async def _read_loop(self) -> None:
        try:
            while True:
                frame = await self._t.receive_json()
                await self._dispatch(frame)
        except WSDisconnect:
            return

    async def _watch_overflow(self) -> None:
        await self._overflow.wait()
        await self._t.close(1011, "backpressure")

    async def _authenticate(self) -> bool:
        try:
            first = await asyncio.wait_for(
                self._t.receive_json(), timeout=AUTH_TIMEOUT_S)
        except (asyncio.TimeoutError, WSDisconnect):
            await self._t.close(4401, "auth timeout")
            return False
        if (not isinstance(first, dict) or first.get("type") != "auth"
                or first.get("token") != self._token):
            await self._t.close(4401, "unauthorized")
            return False
        return True

    def _hello(self) -> dict:
        return {
            "type": "hello",
            "server_version": self._server_version,
            "protocol_version": PROTOCOL_VERSION,
            "constants": self._constants,
            "capabilities": ["compact"],
            "supported_kinds": list(SUPPORTED_KINDS),
        }

    # -- outbound ---------------------------------------------------------

    def _emit(self, frame: dict) -> None:
        try:
            self._out.put_nowait(frame)
        except asyncio.QueueFull:
            self._overflow.set()

    async def _sender(self) -> None:
        while True:
            frame = await self._out.get()
            await self._t.send_json(frame)

    async def _drain_remaining(self) -> None:
        while not self._out.empty():
            with contextlib.suppress(Exception):
                await self._t.send_json(self._out.get_nowait())

    # -- dispatch ---------------------------------------------------------

    async def _dispatch(self, frame: dict) -> None:
        if not isinstance(frame, dict) or "type" not in frame:
            self._emit({"type": "error", "code": "bad_frame",
                        "message": "missing type"})
            return
        kind = frame["type"]
        if kind == "rpc":
            await self._rpc(frame)
        elif kind == "subscribe":
            await self._subscribe(frame)
        elif kind == "unsubscribe":
            self._unsubscribe(frame)
        elif kind == "resume":
            await self._resume(frame)
        else:
            self._emit({"type": "error", "code": "bad_frame",
                        "message": f"unknown frame type {kind!r}",
                        "id": frame.get("id")})

    # -- rpc --------------------------------------------------------------

    async def _rpc(self, frame: dict) -> None:
        rid = frame.get("id")
        method = frame.get("method")
        params = frame.get("params") or {}
        try:
            result = await self._call(method, params)
        except _RpcUnknown:
            self._emit({"type": "error", "code": "unknown_method",
                        "message": f"unknown method {method!r}", "id": rid})
            return
        except Exception as exc:  # surfaced to the client, not fatal
            self._emit({"type": "rpc_response", "id": rid, "ok": False,
                        "error": str(exc)})
            return
        self._emit({"type": "rpc_response", "id": rid, "ok": True,
                    "result": result})

    async def _call(self, method: str, params: dict) -> dict:
        if method == "list_agents":
            return {"agents": self._m.list_agents()}
        if method == "list_sessions":
            return {"sessions": [asdict(si) for si in self._m.list_sessions()]}
        if method == "spawn_session":
            handle = await self._m.spawn(params["agent_profile"])
            self._reg.broadcast_session_list()
            return {"handle": handle}
        if method == "close_session":
            await self._m.close(params["handle"])
            self._reg.broadcast_session_list()
            return {"ok": True}
        if method == "interrupt_session":
            await self._m.interrupt(params["handle"])
            return {"ok": True}
        if method == "queue_tail":
            return {"lines": self._reg.queue_tail(params["task_id"])}
        if method == "group_status":
            return {"groups": await self._reg.group_status()}
        if method == "file_search":
            return {"paths": self._reg.file_search(params.get("query", ""))}
        if method == "file_read":
            return self._reg.file_read(params["path"])
        if method == "config_show":
            return self._reg.config_show()
        if method == "list_themes":
            from aegis.themes import list_theme_names
            return {"names": list_theme_names()}
        if method == "config_add_agent":
            return await self._reg.config_add_agent(
                params["slug"], provider=params["provider"],
                model=params["model"], effort=params.get("effort"),
                permission=params.get("permission"))
        if method == "config_remove_agent":
            return await self._reg.config_remove_agent(params["slug"])
        if method == "config_add_queue":
            return await self._reg.config_add_queue(
                params["name"], agent=params["agent"],
                max_parallel=params["max_parallel"])
        if method == "config_remove_queue":
            return await self._reg.config_remove_queue(params["name"])
        if method == "deliver":
            core = self._m.get(params["handle"])
            if core is None:
                raise ValueError("unknown handle")
            msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                               body=params["message"])
            receipt = await core.deliver(msg)
            return {"delivery": receipt.disposition, "depth": receipt.depth}
        if method == "get_event":
            return self._reg.get_event(params["handle"], int(params["seq"]))
        if method == "handoff":
            result = await self._m.handoff(
                params["from_handle"], params["target_handle"],
                params["context"])
            return {"result": result}
        if method == "rename_handle":
            return await self._m.rename_handle(
                params["old"], params["new"])
        raise _RpcUnknown(method)

    # -- subscribe / resume ----------------------------------------------

    async def _subscribe(self, frame: dict) -> None:
        target = frame.get("target") or {}
        tail = frame.get("tail")           # optional per-subscription override
        if target.get("kind") == "session":
            await self._open_session(target["handle"], from_seq=0, tail=tail)
        elif (target.get("kind") == "global"
              and target.get("stream") == "session_list"):
            if not self._global_on:
                self._reg.subscribe_global(self._global_sink)
                self._global_on = True
            self._emit(self._reg.session_list_frame())
        elif (target.get("kind") == "global"
              and target.get("stream") == "queue_digest"):
            if not self._queue_on:
                self._reg.subscribe_queue(self._queue_sink)
                self._queue_on = True
            self._emit(self._reg.queue_digest_frame())

    async def _resume(self, frame: dict) -> None:
        for sub in frame.get("subscriptions") or []:
            await self._open_session(
                sub["handle"], from_seq=int(sub.get("last_seq", 0)),
                resume=True, tail=sub.get("tail"))
        if "session_list" in (frame.get("globals") or []):
            await self._subscribe(
                {"target": {"kind": "global", "stream": "session_list"}})

    async def _open_session(self, handle: str, *, from_seq: int,
                            resume: bool = False,
                            tail: int | None = None) -> None:
        """Attach a sink, then stream history (sliced for resume) and go
        live. Live frames that fire during setup are buffered, then flushed
        with seq-dedup so history and live never overlap or gap."""
        hstate = {"buffering": True, "buffer": []}

        def sink(fr: dict) -> None:
            if hstate["buffering"]:
                hstate["buffer"].append(fr)
            else:
                self._emit(fr)

        hstate["sink"] = sink
        self._subs[handle] = hstate
        current = await self._reg.subscribe(handle, sink)

        hist = self._reg.history(handle)
        gap_cap = self._constants.get("RESUME_GAP_CAP", 1000)
        large_gap = resume and (current - from_seq > gap_cap or from_seq > current)
        if resume and large_gap:
            self._emit({"type": "stream", "kind": "window_reset",
                        "handle": handle, "dropped_through_seq": from_seq})
            # Apply per-subscription tail on the fresh-history replay after window_reset
            if tail == 0:
                lower = hist[-1][0] if hist else 0  # skip all history
            else:
                effective_tail = (tail if tail is not None
                                  else self._constants.get("REPLAY_TAIL", 0))
                lower = _tail_lower_seq(hist, effective_tail)
        elif resume:
            lower = from_seq
        else:
            # Fresh open: send only the last REPLAY_TAIL blocks so a long
            # session reloads fast. Older history stays on disk (reachable
            # via get_event / the TUI's full scroll-back).
            if tail == 0:
                lower = hist[-1][0] if hist else 0  # skip all history
            else:
                effective_tail = (tail if tail is not None
                                  else self._constants.get("REPLAY_TAIL", 0))
                lower = _tail_lower_seq(hist, effective_tail)

        for seq, ev in hist:
            if lower < seq <= current:
                self._emit(event_frame(handle, seq, ev))
        self._emit({"type": "stream", "kind": "history_complete",
                    "handle": handle, "current_seq": current})

        hstate["buffering"] = False
        for fr in hstate["buffer"]:
            # Inbox frames carry no seq (they aren't persisted); pass them
            # through. Event frames dedup against history by seq.
            if fr.get("kind") == "inbox" or fr.get("seq", 0) > current:
                self._emit(fr)
        hstate["buffer"].clear()

    def _unsubscribe(self, frame: dict) -> None:
        target = frame.get("target") or {}
        if target.get("kind") == "session":
            self._detach(target.get("handle"))

    def _detach(self, handle: str | None) -> None:
        hstate = self._subs.pop(handle, None)
        if hstate is not None:
            self._reg.unsubscribe(handle, hstate["sink"])

    def _detach_all(self) -> None:
        for handle in list(self._subs):
            self._detach(handle)


class _RpcUnknown(Exception):
    pass
