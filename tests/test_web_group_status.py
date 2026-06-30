from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from aegis.config import WebConfig
from aegis.mcp.bridge import SessionInfo
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSSession, WSDisconnect

CONSTANTS = {"RESUME_GAP_CAP": 1000}
_DISCO = object()


class FakeGroups:
    def __init__(self):
        self.runtime = SimpleNamespace(
            registry=SimpleNamespace(names=lambda: ["red"]))

    async def status(self, name):
        return {
            "name": name,
            "members": [{"handle": "w1", "profile": "opus"}],
            "current_broadcast": {
                "id": "b1", "objective": "do the thing",
                "started_at": "t0", "members": ["w1"]},
        }


class FakeManager:
    def __init__(self):
        self.groups = FakeGroups()

    def list_agents(self):
        return ["opus"]

    def list_sessions(self):
        return [SessionInfo(handle="w1", agent_slug="opus", state="working",
                            active=True, unseen=False)]

    def get(self, h):
        return None


class FakeTransport:
    def __init__(self):
        self._in: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = None

    def feed(self, f):
        self._in.put_nowait(f)

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


async def _settle(n=4):
    for _ in range(n):
        await asyncio.sleep(0.01)


async def test_group_status_rpc(tmp_path: Path):
    mgr = FakeManager()
    reg = SubscriptionRegistry(mgr, tmp_path / "state")
    t = FakeTransport()
    sess = WSSession(t, mgr, reg, WebConfig(token="s"), CONSTANTS)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "s"})
    await _settle()
    t.feed({"type": "rpc", "id": 1, "method": "group_status"})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True
    groups = resp["result"]["groups"]
    assert groups[0]["name"] == "red"
    m = groups[0]["members"][0]
    assert m["handle"] == "w1" and m["profile"] == "opus"
    assert m["state"] == "working"          # enriched from list_sessions
    assert groups[0]["current_broadcast"]["objective"] == "do the thing"
    t.disconnect()
    await asyncio.wait_for(task, timeout=1.0)


async def test_group_status_no_groups(tmp_path: Path):
    class Bare:
        def list_agents(self): return []
        def list_sessions(self): return []
        def get(self, h): return None
    reg = SubscriptionRegistry(Bare(), tmp_path / "state")
    assert await reg.group_status() == []
