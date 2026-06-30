from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.config import WebConfig
from aegis.mcp.bridge import SessionInfo
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSSession, WSDisconnect

CONSTANTS = {"RESUME_GAP_CAP": 1000}
_DISCO = object()


class FakeTransport:
    def __init__(self) -> None:
        self._in: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = None

    def feed(self, frame):
        self._in.put_nowait(frame)

    def disconnect(self):
        self._in.put_nowait(_DISCO)

    async def receive_json(self):
        f = await self._in.get()
        if f is _DISCO:
            raise WSDisconnect()
        return f

    async def send_json(self, obj):
        self.sent.append(obj)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class FakeManager:
    def __init__(self):
        self._cores: dict[str, object] = {}

    def list_agents(self):
        return ["opus"]

    def list_sessions(self):
        return [SessionInfo(handle=h, agent_slug="opus", state="ready",
                            active=True, unseen=False)
                for h in self._cores]

    def get(self, h):
        return self._cores.get(h)

    async def spawn(self, profile):
        h = f"a{len(self._cores) + 1}"
        self._cores[h] = object()
        return h

    async def close(self, h):
        self._cores.pop(h, None)

    async def interrupt(self, h):
        pass


async def _settle(n=4):
    for _ in range(n):
        await asyncio.sleep(0.01)


def _sl_frames(t):
    return [s for s in t.sent if s.get("kind") == "session_list"]


async def test_global_subscriber_sees_spawn_and_close(tmp_path: Path):
    mgr = FakeManager()
    reg = SubscriptionRegistry(mgr, tmp_path / "state")
    cfg = WebConfig(token="s")
    tA, tB = FakeTransport(), FakeTransport()
    a = WSSession(tA, mgr, reg, cfg, CONSTANTS)
    b = WSSession(tB, mgr, reg, cfg, CONSTANTS)
    ta = asyncio.create_task(a.run())
    tb = asyncio.create_task(b.run())
    tA.feed({"type": "auth", "token": "s"})
    tB.feed({"type": "auth", "token": "s"})
    await _settle()

    # A subscribes to the global session list → initial (empty) snapshot
    tA.feed({"type": "subscribe",
             "target": {"kind": "global", "stream": "session_list"}})
    await _settle()
    assert _sl_frames(tA)[-1]["sessions"] == []

    # B spawns a session → A is notified with the new handle
    tB.feed({"type": "rpc", "id": 1, "method": "spawn_session",
             "params": {"agent_profile": "opus"}})
    await _settle()
    handles = [s["handle"] for s in _sl_frames(tA)[-1]["sessions"]]
    assert "a1" in handles

    # B closes it → A sees it gone
    tB.feed({"type": "rpc", "id": 2, "method": "close_session",
             "params": {"handle": "a1"}})
    await _settle()
    assert _sl_frames(tA)[-1]["sessions"] == []

    tA.disconnect(); tB.disconnect()
    await asyncio.wait_for(ta, timeout=1.0)
    await asyncio.wait_for(tb, timeout=1.0)
