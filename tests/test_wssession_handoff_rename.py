from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.config import WebConfig
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSSession, WSDisconnect

from aegis.transcript_constants import REPLAY_TAIL as _REPLAY_TAIL

CONSTANTS = {"N_MAX": 300, "RESUME_GAP_CAP": 1000, "REPLAY_TAIL": _REPLAY_TAIL}
_DISCO = object()


class FakeTransport:
    def __init__(self) -> None:
        self._in: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self._send_gate: asyncio.Event | None = None

    def feed(self, frame: dict) -> None:
        self._in.put_nowait(frame)

    def disconnect(self) -> None:
        self._in.put_nowait(_DISCO)

    async def receive_json(self) -> dict:
        f = await self._in.get()
        if f is _DISCO:
            raise WSDisconnect()
        return f

    async def send_json(self, obj: dict) -> None:
        if self._send_gate is not None:
            await self._send_gate.wait()
        self.sent.append(obj)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


class _Metrics:
    def render(self, now: float) -> str:
        return "m"


class FakeCore:
    def __init__(self, handle: str) -> None:
        self.handle = handle
        self.metrics = _Metrics()
        self._ev: list = []
        self._st: list = []
        self._ib: list = []
        self.delivered: list = []

    def add_event_observer(self, cb):
        self._ev.append(cb)

    def add_state_observer(self, cb):
        self._st.append(cb)

    def add_inbox_observer(self, cb):
        self._ib.append(cb)

    def emit_event(self, ev):
        for cb in list(self._ev):
            cb(self, ev)

    async def deliver(self, msg):
        from aegis.queue.schema import Delivery
        self.delivered.append(msg)
        return Delivery(disposition="landed", depth=0)


class FakeManager:
    def __init__(self, cores: dict | None = None) -> None:
        self._cores = cores or {}
        self.spawned: list = []
        self.handoff_calls: list = []
        self.handoff_result: str = ""
        self.rename_result: dict | None = None

    def list_agents(self):
        return ["claude", "gemini"]

    def list_sessions(self):
        return []

    def get(self, handle):
        return self._cores.get(handle)

    async def spawn(self, profile):
        self.spawned.append(profile)
        h = f"agent-{len(self.spawned)}"
        self._cores[h] = FakeCore(h)
        return h

    async def close(self, handle):
        self._cores.pop(handle, None)

    async def interrupt(self, handle):
        pass

    async def handoff(self, from_handle: str, target_handle: str, context: str) -> str:
        self.handoff_calls.append((from_handle, target_handle, context))
        return self.handoff_result

    async def rename_handle(self, old: str, new: str) -> dict:
        return self.rename_result or {"old": old, "new": new}


def _cfg() -> WebConfig:
    return WebConfig(token="secret")


def _session(t, mgr, reg, **kw) -> WSSession:
    return WSSession(t, mgr, reg, _cfg(), CONSTANTS, **kw)


async def _settle(n: int = 3) -> None:
    for _ in range(n):
        await asyncio.sleep(0.01)


async def _run_authed(tmp_path, mgr):
    reg = SubscriptionRegistry(mgr, tmp_path / "state")
    t = FakeTransport()
    sess = _session(t, mgr, reg)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "secret"})
    await _settle()
    return t, reg, task


# ---- handoff & rename_handle ----

async def test_handoff_rpc_calls_manager(tmp_path: Path):
    mgr = FakeManager()
    mgr.handoff_result = "delivered"
    t, _, task = await _run_authed(tmp_path, mgr)
    t.feed({"type": "rpc", "id": 1, "method": "handoff",
            "params": {"from_handle": "a", "target_handle": "b",
                       "context": "please pick up"}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp == {"type": "rpc_response", "id": 1, "ok": True,
                    "result": {"result": "delivered"}}
    assert mgr.handoff_calls == [("a", "b", "please pick up")]
    t.disconnect()
    await task


async def test_rename_handle_rpc_calls_manager(tmp_path: Path):
    mgr = FakeManager()
    mgr.rename_result = {"old": "swift-bohr", "new": "quiet-turing"}
    t, _, task = await _run_authed(tmp_path, mgr)
    t.feed({"type": "rpc", "id": 7, "method": "rename_handle",
            "params": {"old": "swift-bohr", "new": "quiet-turing"}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True
    assert resp["result"] == {"old": "swift-bohr", "new": "quiet-turing"}
    t.disconnect()
    await task
