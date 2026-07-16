"""Tests for optional per-subscription `tail` field on subscribe and resume.

These tests verify Task 2: the server threads an optional `tail: int` through
`_subscribe` / `_resume` / `_open_session` so clients can override the
server-side `REPLAY_TAIL` default on a per-subscription basis.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.config import WebConfig
from aegis.events import AssistantText, ToolUse
from aegis.state.session_log import append_event
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSSession, WSDisconnect

_DISCO = object()


class FakeTransport:
    def __init__(self) -> None:
        self._in: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

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

    def add_event_observer(self, cb):
        self._ev.append(cb)

    def add_state_observer(self, cb):
        self._st.append(cb)

    def add_inbox_observer(self, cb):
        self._ib.append(cb)


class FakeManager:
    def __init__(self, cores: dict | None = None) -> None:
        self._cores = cores or {}

    def list_agents(self):
        return []

    def list_sessions(self):
        return []

    def get(self, handle):
        return self._cores.get(handle)


def _cfg() -> WebConfig:
    return WebConfig(token="secret")


def _make_session(t, mgr, reg, constants: dict) -> WSSession:
    return WSSession(t, mgr, reg, _cfg(), constants)


async def _settle(n: int = 5) -> None:
    for _ in range(n):
        await asyncio.sleep(0.01)


async def _run_authed(state_dir: Path, mgr: FakeManager, constants: dict):
    """Create a WSSession, authenticate it, and return (transport, reg, task)."""
    reg = SubscriptionRegistry(mgr, state_dir)
    t = FakeTransport()
    sess = _make_session(t, mgr, reg, constants)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "secret"})
    await _settle()
    return t, reg, task


# ---------------------------------------------------------------------------
# Test 1: subscribe with explicit tail=2 overrides REPLAY_TAIL=10
# ---------------------------------------------------------------------------

async def test_subscribe_tail_override_wins_over_constant(tmp_path: Path):
    """subscribe(tail=2) must send only the last 2 coalesced blocks even
    when REPLAY_TAIL is 10."""
    sd = tmp_path / "state"
    # Seed 10 ToolUse events — each is its own coalesced block (seq 1..10)
    for i in range(1, 11):
        append_event(sd, "swift-bohr",
                     ToolUse(name="Read", summary=f"f{i}", kind="read"))

    mgr = FakeManager({"swift-bohr": FakeCore("swift-bohr")})
    constants = {"REPLAY_TAIL": 10, "RESUME_GAP_CAP": 1000}

    t, reg, task = await _run_authed(sd, mgr, constants)
    t.feed({
        "type": "subscribe",
        "tail": 2,
        "target": {"kind": "session", "handle": "swift-bohr"},
    })
    await _settle()

    seqs = [fr["seq"] for fr in t.sent if fr.get("kind") == "event"]
    assert seqs == [9, 10], f"expected [9, 10], got {seqs}"

    t.disconnect()
    await task


# ---------------------------------------------------------------------------
# Test 2: resume with large gap + per-subscription tail=3
# ---------------------------------------------------------------------------

async def test_resume_per_subscription_tail_used_on_large_gap(tmp_path: Path):
    """resume with a >gap_cap gap and tail=3 sends window_reset then last 3 events."""
    sd = tmp_path / "state"
    # Seed 500 ToolUse events — each is its own coalesced block (seq 1..500)
    for i in range(1, 501):
        append_event(sd, "swift-bohr",
                     ToolUse(name="Read", summary=f"f{i}", kind="read"))

    mgr = FakeManager({"swift-bohr": FakeCore("swift-bohr")})
    # RESUME_GAP_CAP=100 so gap of (500-1)=499 > 100 → large_gap triggers
    constants = {"REPLAY_TAIL": 10, "RESUME_GAP_CAP": 100}

    t, reg, task = await _run_authed(sd, mgr, constants)
    t.feed({
        "type": "resume",
        "subscriptions": [{"handle": "swift-bohr", "last_seq": 1, "tail": 3}],
    })
    await _settle()

    kinds = [fr.get("kind") for fr in t.sent if "kind" in fr]

    # window_reset must appear before the events
    wr_frames = [fr for fr in t.sent if fr.get("kind") == "window_reset"]
    assert wr_frames, "expected a window_reset frame"
    assert wr_frames[0]["dropped_through_seq"] == 1

    # Exactly the last 3 events (seqs 498, 499, 500)
    event_seqs = [fr["seq"] for fr in t.sent if fr.get("kind") == "event"]
    assert event_seqs == [498, 499, 500], f"expected [498, 499, 500], got {event_seqs}"

    # history_complete must appear after events
    hc_frames = [fr for fr in t.sent if fr.get("kind") == "history_complete"]
    assert hc_frames, "expected a history_complete frame"
    assert hc_frames[0]["current_seq"] == 500

    t.disconnect()
    await task


# ---------------------------------------------------------------------------
# Test 3: subscribe with tail=0 replays no history
# ---------------------------------------------------------------------------

async def test_subscribe_tail_zero_replays_nothing(tmp_path: Path):
    """subscribe(tail=0) must send history_complete with no event frames before it,
    going live-only even when REPLAY_TAIL is 10."""
    sd = tmp_path / "state"
    # Seed 5 ToolUse events — each is its own coalesced block (seq 1..5)
    for i in range(1, 6):
        append_event(sd, "swift-bohr",
                     ToolUse(name="Read", summary=f"f{i}", kind="read"))

    mgr = FakeManager({"swift-bohr": FakeCore("swift-bohr")})
    constants = {"REPLAY_TAIL": 10, "RESUME_GAP_CAP": 1000}

    t, reg, task = await _run_authed(sd, mgr, constants)
    t.feed({
        "type": "subscribe",
        "tail": 0,
        "target": {"kind": "session", "handle": "swift-bohr"},
    })
    await _settle()

    # No event frames should be sent
    event_seqs = [fr["seq"] for fr in t.sent if fr.get("kind") == "event"]
    assert event_seqs == [], f"expected no event frames, got {event_seqs}"

    # history_complete must appear and show current_seq=5
    hc_frames = [fr for fr in t.sent if fr.get("kind") == "history_complete"]
    assert hc_frames, "expected a history_complete frame"
    assert hc_frames[0]["current_seq"] == 5

    # Verify live observer is still wired: inject a live event (seq 6)
    core = mgr.get("swift-bohr")
    t.sent.clear()
    for cb in core._ev:
        cb(6, ToolUse(name="Read", summary="f6", kind="read"))
    await _settle()

    # The live event should arrive
    live_seqs = [fr["seq"] for fr in t.sent if fr.get("kind") == "event"]
    assert live_seqs == [6], f"expected live event [6], got {live_seqs}"

    t.disconnect()
    await task
