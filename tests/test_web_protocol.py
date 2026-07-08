from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.config import WebConfig
from aegis.events import AssistantText
from aegis.queue.schema import Delivery
from aegis.state.session_log import append_event
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


class _State:
    def __init__(self, value: str) -> None:
        self.value = value


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
        self.delivered.append(msg)
        return Delivery(disposition="landed", depth=0)


class FakeManager:
    def __init__(self, cores: dict | None = None) -> None:
        self._cores = cores or {}
        self.spawned: list = []

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


def _cfg() -> WebConfig:
    return WebConfig(token="secret")


def _session(t, mgr, reg, **kw) -> WSSession:
    return WSSession(t, mgr, reg, _cfg(), CONSTANTS, **kw)


async def _settle(n: int = 3) -> None:
    for _ in range(n):
        await asyncio.sleep(0.01)


async def _run_authed(tmp_path, mgr, cores_state_dir=None):
    reg = SubscriptionRegistry(mgr, cores_state_dir or (tmp_path / "state"))
    t = FakeTransport()
    sess = _session(t, mgr, reg)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "secret"})
    await _settle()
    return t, reg, task


# ---- auth ---------------------------------------------------------------

async def test_auth_success_sends_hello(tmp_path: Path):
    t, _, task = await _run_authed(tmp_path, FakeManager())
    hello = t.sent[0]
    assert hello["type"] == "hello"
    assert hello["protocol_version"] == 2
    assert hello["constants"]["RESUME_GAP_CAP"] == 1000
    assert "event" in hello["supported_kinds"]
    assert "compact" in hello["capabilities"]
    t.disconnect()
    await task


async def test_bad_token_closes_4401(tmp_path: Path):
    reg = SubscriptionRegistry(FakeManager(), tmp_path / "state")
    t = FakeTransport()
    sess = _session(t, FakeManager(), reg)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "WRONG"})
    await task
    assert t.closed is not None and t.closed[0] == 4401
    assert t.sent == []


async def test_non_auth_first_frame_closes_4401(tmp_path: Path):
    reg = SubscriptionRegistry(FakeManager(), tmp_path / "state")
    t = FakeTransport()
    sess = _session(t, FakeManager(), reg)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "rpc", "id": 1, "method": "list_agents"})
    await task
    assert t.closed is not None and t.closed[0] == 4401


# ---- rpc ----------------------------------------------------------------

async def test_rpc_list_agents(tmp_path: Path):
    t, _, task = await _run_authed(tmp_path, FakeManager())
    t.feed({"type": "rpc", "id": 7, "method": "list_agents"})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["id"] == 7 and resp["ok"] is True
    assert resp["result"]["agents"] == ["claude", "gemini"]
    t.disconnect()
    await task


async def test_rpc_spawn_session(tmp_path: Path):
    mgr = FakeManager()
    t, _, task = await _run_authed(tmp_path, mgr)
    t.feed({"type": "rpc", "id": 1, "method": "spawn_session",
            "params": {"agent_profile": "claude"}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True and resp["result"]["handle"] == "agent-1"
    assert mgr.spawned == ["claude"]
    t.disconnect()
    await task


async def test_rpc_deliver_records_message(tmp_path: Path):
    core = FakeCore("h")
    mgr = FakeManager({"h": core})
    t, _, task = await _run_authed(tmp_path, mgr)
    t.feed({"type": "rpc", "id": 2, "method": "deliver",
            "params": {"handle": "h", "message": "hello agent"}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True
    assert resp["result"]["delivery"] == "landed"
    assert core.delivered[0].body == "hello agent"
    assert core.delivered[0].sender  # sender_user tag set
    t.disconnect()
    await task


async def test_rpc_unknown_method_errors(tmp_path: Path):
    t, _, task = await _run_authed(tmp_path, FakeManager())
    t.feed({"type": "rpc", "id": 9, "method": "nope"})
    await _settle()
    err = [s for s in t.sent if s.get("type") == "error"][-1]
    assert err["code"] == "unknown_method" and err["id"] == 9
    t.disconnect()
    await task


async def test_rpc_get_event_returns_full_body(tmp_path: Path):
    from aegis.events import ToolResult
    sd = tmp_path / "state"
    body = "\n".join(f"L{i}" for i in range(30))
    append_event(sd, "h", ToolResult(text=body, is_error=False))
    mgr = FakeManager({"h": FakeCore("h")})
    t, _, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "rpc", "id": 4, "method": "get_event",
            "params": {"handle": "h", "seq": 1}})
    await _settle()
    resp = [s for s in t.sent if s.get("type") == "rpc_response"][-1]
    assert resp["ok"] is True
    assert resp["result"]["event"]["text"] == body   # full, un-truncated
    t.disconnect()
    await task


# ---- subscribe + history + live ----------------------------------------

async def test_subscribe_streams_history_then_live(tmp_path: Path):
    sd = tmp_path / "state"
    append_event(sd, "h", AssistantText("one"))
    append_event(sd, "h", AssistantText("two"))
    core = FakeCore("h")
    mgr = FakeManager({"h": core})
    t, reg, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "subscribe",
            "target": {"kind": "session", "handle": "h"}})
    await _settle()
    events = [s for s in t.sent if s.get("kind") == "event"]
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["event"]["text"] == "one"
    assert "html" not in events[0]
    hc = [s for s in t.sent if s.get("kind") == "history_complete"][-1]
    assert hc["current_seq"] == 2
    # a live event continues at seq 3
    core.emit_event(AssistantText("three"))
    await _settle()
    live = [s for s in t.sent if s.get("kind") == "event"][-1]
    assert live["seq"] == 3 and live["event"]["text"] == "three"
    t.disconnect()
    await task


async def test_subscribe_streams_only_replay_tail(tmp_path: Path):
    """Fresh open sends only the last REPLAY_TAIL coalesced blocks, not the
    whole history — so a long session reloads fast on the web too."""
    from aegis.events import ToolUse
    from aegis.transcript_constants import REPLAY_TAIL

    sd = tmp_path / "state"
    n = REPLAY_TAIL + 12
    for i in range(n):  # each ToolUse is its own coalesced block
        append_event(sd, "h", ToolUse(name="Read", summary=f"f{i}", kind="read"))
    mgr = FakeManager({"h": FakeCore("h")})
    t, reg, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "subscribe",
            "target": {"kind": "session", "handle": "h"}})
    await _settle()
    events = [s for s in t.sent if s.get("kind") == "event"]
    # Only the last REPLAY_TAIL events (1-based seqs n-REPLAY_TAIL+1 .. n).
    assert [e["seq"] for e in events] == list(range(n - REPLAY_TAIL + 1, n + 1))
    hc = [s for s in t.sent if s.get("kind") == "history_complete"][-1]
    assert hc["current_seq"] == n  # live continues from the true tip
    # A live event still lands at the next seq.
    core = mgr.get("h")
    core.emit_event(ToolUse(name="Read", summary="live", kind="read"))
    await _settle()
    live = [s for s in t.sent if s.get("kind") == "event"][-1]
    assert live["seq"] == n + 1
    t.disconnect()
    await task


async def test_subscribe_tail_keeps_whole_coalesced_block(tmp_path: Path):
    """A run of streaming chunks is ONE block: the tail must not cut it in
    the middle. 20 same-message AssistantText chunks = 1 block ≤ tail."""
    sd = tmp_path / "state"
    for i in range(20):
        append_event(sd, "h", AssistantText(f"chunk{i}"))  # message_id None → one block
    mgr = FakeManager({"h": FakeCore("h")})
    t, reg, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "subscribe",
            "target": {"kind": "session", "handle": "h"}})
    await _settle()
    events = [s for s in t.sent if s.get("kind") == "event"]
    assert [e["seq"] for e in events] == list(range(1, 21))  # all 20, uncut
    t.disconnect()
    await task


# ---- resume -------------------------------------------------------------

async def test_resume_small_gap_streams_only_tail(tmp_path: Path):
    sd = tmp_path / "state"
    for w in ("a", "b", "c", "d"):
        append_event(sd, "h", AssistantText(w))
    mgr = FakeManager({"h": FakeCore("h")})
    t, reg, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "resume",
            "subscriptions": [{"handle": "h", "last_seq": 2}],
            "globals": []})
    await _settle()
    events = [s for s in t.sent if s.get("kind") == "event"]
    assert [e["seq"] for e in events] == [3, 4]  # only the missing tail
    assert not any(s.get("kind") == "window_reset" for s in t.sent)
    t.disconnect()
    await task


async def test_resume_large_gap_sends_window_reset(tmp_path: Path):
    sd = tmp_path / "state"
    append_event(sd, "h", AssistantText("a"))
    append_event(sd, "h", AssistantText("b"))
    mgr = FakeManager({"h": FakeCore("h")})
    t, reg, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    # last_seq far ahead of current → treated as stale → window_reset + full
    t.feed({"type": "resume",
            "subscriptions": [{"handle": "h", "last_seq": 99999}],
            "globals": []})
    await _settle()
    wr = [s for s in t.sent if s.get("kind") == "window_reset"]
    assert wr and wr[0]["dropped_through_seq"] == 99999
    events = [s for s in t.sent if s.get("kind") == "event"]
    assert [e["seq"] for e in events] == [1, 2]  # full history re-sent
    t.disconnect()
    await task


# ---- backpressure -------------------------------------------------------

async def test_backpressure_closes_connection(tmp_path: Path):
    core = FakeCore("h")
    mgr = FakeManager({"h": core})
    reg = SubscriptionRegistry(mgr, tmp_path / "state")
    t = FakeTransport()
    t._send_gate = asyncio.Event()       # block all sends so the queue fills
    sess = _session(t, mgr, reg, send_cap=3)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "secret"})
    t.feed({"type": "subscribe",
            "target": {"kind": "session", "handle": "h"}})
    await _settle()
    # flood synchronously past the cap while sends are gated
    for i in range(20):
        core.emit_event(AssistantText(f"e{i}"))
    await _settle()
    assert t.closed is not None and t.closed[1] == "backpressure"
    t._send_gate.set()
    t.disconnect()
    await asyncio.wait_for(task, timeout=1.0)
