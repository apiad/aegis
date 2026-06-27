from __future__ import annotations

from aegis.queue import Delivery, InboxMessage, InboxRouter


class FakeSession:
    def __init__(self, receipt: Delivery | None = None):
        self.delivered: list[InboxMessage] = []
        self._receipt = receipt or Delivery(disposition="landed", depth=0)

    async def deliver(self, msg: InboxMessage) -> Delivery:
        self.delivered.append(msg)
        return self._receipt


def _msg(sender="queue:impl", body="hi", task_id="01J42", status="ok"):
    return InboxMessage(sender=sender, timestamp="2026-05-20T07:14:00Z",
                        body=body, task_id=task_id, status=status)


async def test_deliver_to_unbound_handle_buffers():
    r = InboxRouter()
    await r.deliver("lucid-knuth", _msg())
    assert len(r.pending("lucid-knuth")) == 1


async def test_deliver_to_unbound_handle_returns_queued_receipt():
    r = InboxRouter()
    r1 = await r.deliver("h", _msg(body="a"))
    r2 = await r.deliver("h", _msg(body="b"))
    assert (r1.disposition, r1.depth) == ("queued", 1)
    assert (r2.disposition, r2.depth) == ("queued", 2)


async def test_deliver_to_bound_session_returns_session_receipt():
    r = InboxRouter()
    r.bind_session("h", FakeSession(Delivery(disposition="landed", depth=0)))
    receipt = await r.deliver("h", _msg())
    assert (receipt.disposition, receipt.depth) == ("landed", 0)


async def test_drain_returns_arrival_order_and_clears():
    r = InboxRouter()
    await r.deliver("h", _msg(body="a"))
    await r.deliver("h", _msg(body="b"))
    drained = r.drain("h")
    assert [m.body for m in drained] == ["a", "b"]
    assert r.drain("h") == []


async def test_deliver_to_bound_session_invokes_deliver_and_does_not_buffer():
    r = InboxRouter()
    fake = FakeSession()
    r.bind_session("h", fake)
    m = _msg(body="x")
    await r.deliver("h", m)
    assert fake.delivered == [m]
    # session owns flow; router does not double-buffer once bound
    assert r.pending("h") == []


async def test_unbind_falls_back_to_buffering():
    r = InboxRouter()
    fake = FakeSession()
    r.bind_session("h", fake)
    r.unbind_session("h")
    await r.deliver("h", _msg(body="z"))
    assert fake.delivered == []
    assert [m.body for m in r.pending("h")] == ["z"]


async def test_deliver_writes_through_to_jsonl(tmp_path):
    from aegis.queue.jsonl import read_records
    r = InboxRouter(state_dir=tmp_path)
    await r.deliver("h", _msg(body="alpha"))
    log = read_records(tmp_path / "inboxes" / "h.jsonl")
    assert len(log) == 1
    assert log[0]["body"] == "alpha"
    assert log[0]["sender"] == "queue:impl"
    assert log[0]["v"] == 1


async def test_deliver_persists_even_when_session_bound(tmp_path):
    # A bound session is poked AND the record lands on disk — the audit
    # log is independent of live delivery state.
    from aegis.queue.jsonl import read_records
    r = InboxRouter(state_dir=tmp_path)
    fake = FakeSession()
    r.bind_session("h", fake)
    await r.deliver("h", _msg(body="audit-me"))
    assert len(fake.delivered) == 1
    log = read_records(tmp_path / "inboxes" / "h.jsonl")
    assert log[0]["body"] == "audit-me"


async def test_deliver_state_dir_none_skips_disk(tmp_path):
    # VS1 behavior preserved: no writethrough when state_dir is omitted.
    r = InboxRouter()
    await r.deliver("h", _msg(body="x"))
    assert not (tmp_path / "inboxes").exists()
