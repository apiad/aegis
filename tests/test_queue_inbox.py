from __future__ import annotations

from aegis.queue import InboxMessage, InboxRouter


class FakeSession:
    def __init__(self):
        self.delivered: list[InboxMessage] = []

    async def deliver(self, msg: InboxMessage) -> None:
        self.delivered.append(msg)


def _msg(sender="queue:impl", body="hi", task_id="01J42", status="ok"):
    return InboxMessage(sender=sender, timestamp="2026-05-20T07:14:00Z",
                        body=body, task_id=task_id, status=status)


async def test_deliver_to_unbound_handle_buffers():
    r = InboxRouter()
    await r.deliver("lucid-knuth", _msg())
    assert len(r.pending("lucid-knuth")) == 1


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
