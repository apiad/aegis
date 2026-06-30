from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from aegis.config import WebConfig
from aegis.queue import QueueDigest
from aegis.queue.events import (
    QueueDispatched, QueueEnqueued, QueueStarted,
)
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSSession, WSDisconnect

CONSTANTS = {"RESUME_GAP_CAP": 1000}
_DISCO = object()


class FakeQM:
    """Minimal QueueManager stand-in that QueueDigest can consume."""
    def __init__(self, queues: dict) -> None:
        self._queues = queues
        self._subs: list = []
        self._assistant_text_hook = None

    def subscribe(self, cb):
        self._subs.append(cb)
        return lambda: self._subs.remove(cb)

    def emit(self, ev):
        for cb in list(self._subs):
            cb(ev)


def _digest_with_running_task() -> QueueDigest:
    qspec = SimpleNamespace(name="build", agent_profile="opus", max_parallel=2)
    qm = FakeQM({"build": qspec})
    digest = QueueDigest(qm)
    digest.start()
    qm.emit(QueueEnqueued(task_id="t1", queue="build",
                          payload="do the thing\nmore", enqueued_by="user"))
    qm.emit(QueueDispatched(task_id="t1", queue="build",
                            worker_handle="w1", agent_slug="opus"))
    qm.emit(QueueStarted(task_id="t1", queue="build"))
    digest.record_assistant_text("w1", "working on it")
    return digest


def test_queue_digest_frame_serializes_snapshot(tmp_path: Path):
    reg = SubscriptionRegistry(object(), tmp_path / "state")
    reg.set_digest(_digest_with_running_task())
    frame = reg.queue_digest_frame()
    assert frame["kind"] == "queue_digest"
    q = frame["queues"][0]
    assert q["name"] == "build" and q["agent"] == "opus"
    assert q["running"] == 1 and q["queued"] == 0
    t = next(t for t in frame["tasks"] if t["task_id"] == "t1")
    assert t["state"] == "running" and t["worker_handle"] == "w1"
    assert "do the thing" in t["payload_summary"]


def test_queue_tail(tmp_path: Path):
    reg = SubscriptionRegistry(object(), tmp_path / "state")
    reg.set_digest(_digest_with_running_task())
    assert reg.queue_tail("t1") == ["working on it"]


def test_no_digest_yields_empty_frame(tmp_path: Path):
    reg = SubscriptionRegistry(object(), tmp_path / "state")
    frame = reg.queue_digest_frame()
    assert frame["kind"] == "queue_digest"
    assert frame["queues"] == [] and frame["tasks"] == []
    assert reg.queue_tail("nope") == []


# ---- WSSession integration ----

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


class FakeSM:
    def list_agents(self):
        return ["opus"]

    def list_sessions(self):
        return []

    def get(self, h):
        return None


async def _settle(n=4):
    for _ in range(n):
        await asyncio.sleep(0.01)


async def test_wssession_queue_digest_subscribe_and_tail(tmp_path: Path):
    reg = SubscriptionRegistry(FakeSM(), tmp_path / "state")
    reg.set_digest(_digest_with_running_task())
    t = FakeTransport()
    sess = WSSession(t, FakeSM(), reg, WebConfig(token="s"), CONSTANTS)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "s"})
    await _settle()
    t.feed({"type": "subscribe",
            "target": {"kind": "global", "stream": "queue_digest"}})
    await _settle()
    qd = [s for s in t.sent if s.get("kind") == "queue_digest"]
    assert qd and qd[-1]["queues"][0]["name"] == "build"

    # a later broadcast reaches the subscriber
    reg.broadcast_queue_digest()
    await _settle()
    assert len([s for s in t.sent if s.get("kind") == "queue_digest"]) >= 2

    # queue_tail rpc
    t.feed({"type": "rpc", "id": 1, "method": "queue_tail",
            "params": {"task_id": "t1"}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True and resp["result"]["lines"] == ["working on it"]

    t.disconnect()
    await asyncio.wait_for(task, timeout=1.0)
