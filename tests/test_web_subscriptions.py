from __future__ import annotations

from pathlib import Path

from aegis.events import AssistantText
from aegis.queue.schema import InboxMessage
from aegis.web.subscriptions import SubscriptionRegistry


class _FakeState:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeMetrics:
    def render(self, now: float) -> str:
        return "metrics-str"


class _FakeCore:
    def __init__(self, handle: str) -> None:
        self.handle = handle
        self.metrics = _FakeMetrics()
        self._ev: list = []
        self._st: list = []
        self._ib: list = []

    def add_event_observer(self, cb):
        self._ev.append(cb)

    def add_state_observer(self, cb):
        self._st.append(cb)

    def add_inbox_observer(self, cb):
        self._ib.append(cb)

    def emit_event(self, ev):
        for cb in list(self._ev):
            cb(self, ev)

    def emit_state(self, state, finished=True):
        for cb in list(self._st):
            cb(self, state, finished)

    def emit_inbox(self, msg):
        for cb in list(self._ib):
            cb(self, msg)


class _FakeManager:
    def __init__(self, cores: dict) -> None:
        self._cores = cores

    def get(self, handle: str):
        return self._cores.get(handle)


def _reg(tmp_path: Path, cores: dict) -> SubscriptionRegistry:
    return SubscriptionRegistry(_FakeManager(cores), tmp_path / "state")


async def test_first_subscribe_attaches_once(tmp_path: Path):
    core = _FakeCore("h")
    reg = _reg(tmp_path, {"h": core})
    cur = await reg.subscribe("h", [].append)
    assert cur == 0                       # no persisted history
    assert len(core._ev) == 1
    # second sink on same handle does NOT re-attach observers
    await reg.subscribe("h", [].append)
    assert len(core._ev) == 1
    assert len(core._st) == 1
    assert len(core._ib) == 1


async def test_event_fans_out_with_monotonic_seq(tmp_path: Path):
    core = _FakeCore("h")
    reg = _reg(tmp_path, {"h": core})
    a: list = []
    b: list = []
    await reg.subscribe("h", a.append)
    await reg.subscribe("h", b.append)
    core.emit_event(AssistantText("hi"))
    assert len(a) == 1 and len(b) == 1
    f = a[0]
    assert f["type"] == "stream" and f["kind"] == "event"
    assert f["handle"] == "h" and f["seq"] == 1
    assert f["event_type"] == "AssistantText"
    assert "html" not in f  # rendered client-side
    assert f["event"]["text"] == "hi"
    assert f["event"]["t"] == "AssistantText"  # encoded event dict present
    core.emit_event(AssistantText("two"))
    assert a[1]["seq"] == 2


async def test_state_frame_shape(tmp_path: Path):
    core = _FakeCore("h")
    reg = _reg(tmp_path, {"h": core})
    a: list = []
    await reg.subscribe("h", a.append)
    core.emit_state(_FakeState("working"))
    f = a[0]
    assert f["kind"] == "state" and f["state"] == "working"
    assert f["metrics"] == "metrics-str"
    assert "seq" not in f


async def test_inbox_has_no_seq_and_preserves_event_seq(tmp_path: Path):
    # Inbox messages are rendered but not persisted to the JSONL, so they must
    # NOT consume an event seq — otherwise client seq drifts off the disk line
    # index that get_event resolves against.
    core = _FakeCore("h")
    reg = _reg(tmp_path, {"h": core})
    a: list = []
    await reg.subscribe("h", a.append)
    core.emit_event(AssistantText("one"))           # event seq 1
    msg = InboxMessage(sender="peer", timestamp="t", body="hello",
                       task_id="x", status="ok")
    core.emit_inbox(msg)                            # no seq, no bump
    core.emit_event(AssistantText("two"))           # event seq 2, not 3
    inbox = [f for f in a if f["kind"] == "inbox"][0]
    assert "seq" not in inbox
    assert inbox["msg"]["sender"] == "peer" and inbox["msg"]["body"] == "hello"
    assert inbox["msg"]["task_id"] == "x"
    events = [f for f in a if f["kind"] == "event"]
    assert [e["seq"] for e in events] == [1, 2]


async def test_unsubscribe_stops_delivery(tmp_path: Path):
    core = _FakeCore("h")
    reg = _reg(tmp_path, {"h": core})
    a: list = []
    sink = a.append          # capture one stable reference
    await reg.subscribe("h", sink)
    reg.unsubscribe("h", sink)
    core.emit_event(AssistantText("ignored"))
    assert a == []
