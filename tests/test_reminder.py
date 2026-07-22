"""Self-reminder feature.

Two layers:
- Turn-end reminders on ``AgentSession`` — the lowest-priority tier in
  ``_chain_if_pending``: strictly behind buffered inbox messages AND behind
  an unsolicited harness-event drain.
- ``ReminderService`` — turn-end routing + future-time asyncio timers +
  duration parsing.
Plus MCP surface registration.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.mcp.server import BRIEFING, build_server
from aegis.queue import InboxMessage, ReminderService, sender_reminder
from aegis.queue.reminder import parse_after
from aegis.tui.state import AgentState


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class FakeHarness:
    def __init__(self, events_per_turn):
        self._turns = list(events_per_turn)
        self.started = False
        self.closed = False
        self.sent: list[str] = []

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    async def events(self):
        evs = self._turns.pop(0) if self._turns else []
        for e in evs:
            await asyncio.sleep(0)
            yield e


class PendingHarness(FakeHarness):
    """Reports ``pending_after`` spontaneous-event turns before going quiet —
    each ``has_pending_event()`` True consumes the next ``events()`` turn as an
    unsolicited drain (no ``send``)."""

    def __init__(self, events_per_turn, pending_after: int):
        super().__init__(events_per_turn)
        self._pending_remaining = pending_after

    def has_pending_event(self) -> bool:
        if self._pending_remaining > 0:
            self._pending_remaining -= 1
            return True
        return False


def _turn(text):
    return [AssistantText(text=text),
            Result(duration_ms=1, is_error=False, usage=None)]


def _remind(body):
    return InboxMessage(sender=sender_reminder(),
                        timestamp="2026-07-22T00:00:00Z", body=body)


def _inbox(body):
    return InboxMessage(sender="queue:impl", timestamp="2026-07-22T00:00:00Z",
                        body=body, task_id="01J", status="ok")


# --------------------------------------------------------------------------
# Turn-end reminders on AgentSession
# --------------------------------------------------------------------------
async def test_reminder_fires_as_last_turn():
    h = FakeHarness([_turn("work"), _turn("reminded")])
    s = AgentSession(h, agent=None, agent_slug="d", handle="h")
    await s.send("work")
    s.add_reminder(_remind("circle back"))    # left mid-turn
    await s._task                              # work turn → chain fires reminder
    await s._task                              # reminder turn
    assert s.state is AgentState.ready
    assert len(h.sent) == 2
    assert h.sent[0] == "work"
    assert "> from reminder" in h.sent[1]
    assert "circle back" in h.sent[1]


async def test_reminder_fires_after_buffered_inbox():
    h = FakeHarness([_turn("work"), _turn("inbox-reply"), _turn("reminded")])
    s = AgentSession(h, agent=None, agent_slug="d", handle="h")
    await s.send("work")
    s.add_reminder(_remind("last thing"))
    await s.deliver(_inbox("callback"))        # buffers (working)
    await s._task                              # work turn → drains inbox first
    await s._task                              # inbox turn → then reminder
    await s._task                              # reminder turn
    assert len(h.sent) == 3
    # inbox callback consumed before the reminder
    assert "callback" in h.sent[1]
    assert "last thing" in h.sent[2]
    assert "> from reminder" in h.sent[2]


async def test_reminder_fires_after_unsolicited_drain():
    seen: list[str] = []
    h = PendingHarness([_turn("work"), _turn("spontaneous"), _turn("reminded")],
                       pending_after=1)
    s = AgentSession(h, agent=None, agent_slug="d", handle="h")
    s.on_event = lambda _s, ev: (
        seen.append(ev.text) if isinstance(ev, AssistantText) else None)
    await s.send("work")
    s.add_reminder(_remind("after drain"))
    await s._task                              # work → chain → unsolicited drain
    await s._task                              # unsolicited turn → chain → reminder
    await s._task                              # reminder turn
    # The spontaneous drain landed before the reminder.
    assert seen == ["work", "spontaneous", "reminded"]
    assert "after drain" in h.sent[-1]


async def test_multiple_reminders_batch_into_one_turn():
    h = FakeHarness([_turn("work"), _turn("reminded")])
    s = AgentSession(h, agent=None, agent_slug="d", handle="h")
    await s.send("work")
    s.add_reminder(_remind("first"))
    s.add_reminder(_remind("second"))
    await s._task
    await s._task
    assert len(h.sent) == 2
    body = h.sent[1]
    assert body.count("> from reminder") == 2
    assert "first" in body and "second" in body


async def test_reminder_left_while_idle_promotes_immediately():
    h = FakeHarness([_turn("reminded")])
    s = AgentSession(h, agent=None, agent_slug="d", handle="h")
    assert s.state is AgentState.ready
    s.add_reminder(_remind("wake up"))         # idle → promote now
    assert s._task is not None
    await s._task
    assert len(h.sent) == 1
    assert "wake up" in h.sent[0]


# --------------------------------------------------------------------------
# ReminderService
# --------------------------------------------------------------------------
class FakeInbox:
    def __init__(self):
        self.delivered: list[tuple[str, InboxMessage]] = []

    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg))
        return None


class RecordingSession:
    def __init__(self):
        self.reminders: list[InboxMessage] = []

    def add_reminder(self, msg):
        self.reminders.append(msg)


class FakeSM:
    def __init__(self, session=None):
        self._session = session

    def get(self, handle):
        return self._session


async def test_service_turn_end_routes_to_session():
    sess = RecordingSession()
    svc = ReminderService(FakeInbox(), FakeSM(sess))
    out = svc.remind(from_handle="h", note="later")
    assert out["when"] == "turn_end"
    assert "reminder_id" in out
    assert len(sess.reminders) == 1
    assert sess.reminders[0].sender == sender_reminder()
    assert sess.reminders[0].body == "later"


async def test_service_turn_end_no_session_errors():
    svc = ReminderService(FakeInbox(), FakeSM(None))
    out = svc.remind(from_handle="ghost", note="x")
    assert "error" in out


async def test_service_future_delivers_to_inbox():
    inbox = FakeInbox()
    svc = ReminderService(inbox, FakeSM(None))
    out = svc.remind(from_handle="h", note="ping", after=0.01)
    assert out["when"] != "turn_end"
    assert svc.list_reminders(from_handle="h")     # pending until it fires
    await asyncio.sleep(0.05)
    assert len(inbox.delivered) == 1
    handle, msg = inbox.delivered[0]
    assert handle == "h"
    assert msg.sender == sender_reminder()
    assert msg.body == "ping"
    assert svc.list_reminders() == []              # cleared after firing


async def test_service_cancel_prevents_delivery():
    inbox = FakeInbox()
    svc = ReminderService(inbox, FakeSM(None))
    out = svc.remind(from_handle="h", note="never", after=5)
    res = svc.cancel(out["reminder_id"])
    assert res["ok"] is True
    await asyncio.sleep(0.02)
    assert inbox.delivered == []
    assert svc.list_reminders() == []


async def test_service_cancel_unknown():
    svc = ReminderService(FakeInbox(), FakeSM(None))
    assert svc.cancel("nope")["ok"] is False


async def test_service_list_scopes_by_handle():
    svc = ReminderService(FakeInbox(), FakeSM(None))
    a = svc.remind(from_handle="a", note="x", after=5)
    svc.remind(from_handle="b", note="y", after=5)
    only_a = svc.list_reminders(from_handle="a")
    assert len(only_a) == 1 and only_a[0]["reminder_id"] == a["reminder_id"]
    assert len(svc.list_reminders()) == 2
    svc.reap("a")
    svc.reap("b")
    assert svc.list_reminders() == []


async def test_service_bad_duration_errors():
    svc = ReminderService(FakeInbox(), FakeSM(None))
    assert "error" in svc.remind(from_handle="h", note="x", after="soon")


# --------------------------------------------------------------------------
# Duration parsing
# --------------------------------------------------------------------------
def test_parse_after_numbers_and_bare_string():
    assert parse_after(90) == 90.0
    assert parse_after(90.5) == 90.5
    assert parse_after("90") == 90.0


def test_parse_after_units():
    assert parse_after("30s") == 30.0
    assert parse_after("20m") == 1200.0
    assert parse_after("2h") == 7200.0
    assert parse_after("1d") == 86400.0
    assert parse_after("1h30m") == 5400.0


def test_parse_after_rejects_bad():
    for bad in ["abc", "20x", "", "0", "-5", "10 apples"]:
        with pytest.raises(ValueError):
            parse_after(bad)


# --------------------------------------------------------------------------
# MCP surface
# --------------------------------------------------------------------------
class _Bridge:
    def __init__(self, svc: ReminderService) -> None:
        self.reminder_service = svc


async def _call(server, tool_name: str, **kwargs):
    res = await server.call_tool(tool_name, kwargs)
    if getattr(res, "structured_content", None) is not None:
        sc = res.structured_content
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    return getattr(res, "data", res)


async def test_reminder_tools_registered():
    svc = ReminderService(FakeInbox(), FakeSM(RecordingSession()))
    server = build_server(_Bridge(svc))
    names = {t.name for t in await server.list_tools()}
    assert {"aegis_remind", "aegis_reminders",
            "aegis_reminder_cancel"} <= names


async def test_mcp_remind_turn_end_and_list_cancel():
    sess = RecordingSession()
    svc = ReminderService(FakeInbox(), FakeSM(sess))
    server = build_server(_Bridge(svc))
    out = await _call(server, "aegis_remind", from_handle="h", note="later")
    assert out["when"] == "turn_end"
    assert len(sess.reminders) == 1

    fut = await _call(server, "aegis_remind", from_handle="h",
                      note="soon", after="1h")
    listed = await _call(server, "aegis_reminders", from_handle="h")
    assert any(r["reminder_id"] == fut["reminder_id"] for r in listed)
    res = await _call(server, "aegis_reminder_cancel",
                      reminder_id=fut["reminder_id"])
    assert res["ok"] is True


def test_briefing_mentions_remind():
    assert "aegis_remind" in BRIEFING
