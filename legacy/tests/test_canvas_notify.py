"""Canvas notification → InboxMessage tests."""
from __future__ import annotations

import pytest

from aegis.canvas.manager import CanvasManager, WriteResult
from aegis.canvas.notify import (
    PREVIEW_LINES,
    build_inbox_body,
    build_inbox_message,
    dispatch_notifications,
    make_canvas_notifier,
)
from aegis.queue.inbox import InboxRouter
from aegis.queue.schema import InboxMessage


def _result(**over):
    base = dict(canvas="report-q3", section="data", op="write",
                writer="researcher", added=3, removed=1,
                new_body="line 1\nline 2\nline 3",
                appended_text=None, timestamp="2026-05-21T20:30:00Z")
    base.update(over)
    return WriteResult(**base)


# ---------- body formatting ----------

def test_inbox_body_write_has_diff_math():
    body = build_inbox_body(_result())
    assert "section \"data\"" in body
    assert "written by agent:researcher" in body
    assert "+3 / -1 lines" in body
    assert "line 1" in body


def test_inbox_body_append_shows_only_appended_text():
    body = build_inbox_body(_result(
        op="append", added=1, removed=0,
        appended_text="just this", new_body="old\nold\njust this"))
    assert "appended by agent:researcher" in body
    assert "+1 lines" in body
    # The body should NOT include the "old" lines from new_body
    assert "old" not in body
    assert "just this" in body


def test_inbox_body_truncates_long_preview():
    long = "\n".join(f"line {i}" for i in range(20))
    body = build_inbox_body(_result(new_body=long, added=20))
    assert "line 0" in body
    assert "line 1" in body
    assert "line 5" in body  # PREVIEW_LINES=6 → 0..5 shown
    assert "line 7" not in body  # truncated
    assert f"({20 - PREVIEW_LINES} more lines)" in body


def test_inbox_body_empty_section_shows_placeholder():
    body = build_inbox_body(_result(new_body="", added=0))
    assert "(empty)" in body


# ---------- message wrapper ----------

def test_build_inbox_message_carries_sender_and_timestamp():
    msg = build_inbox_message(_result())
    assert isinstance(msg, InboxMessage)
    assert msg.sender == "canvas:report-q3"
    assert msg.timestamp == "2026-05-21T20:30:00Z"
    assert "section \"data\"" in msg.body


# ---------- dispatch ----------

@pytest.mark.asyncio
async def test_dispatch_skips_self(tmp_path):
    router = InboxRouter(state_dir=tmp_path)
    result = _result(writer="alice")
    delivered = await dispatch_notifications(
        result, ["alice", "bob", "carol"], router)
    assert sorted(delivered) == ["bob", "carol"]


@pytest.mark.asyncio
async def test_dispatch_writes_to_pending_when_no_session(tmp_path):
    router = InboxRouter(state_dir=tmp_path)
    await dispatch_notifications(_result(writer="alice"),
                                 ["bob"], router)
    assert len(router.pending("bob")) == 1


# ---------- notifier integration with CanvasManager ----------

@pytest.mark.asyncio
async def test_notifier_fires_on_write_and_delivers_to_subscribers(tmp_path):
    router = InboxRouter(state_dir=tmp_path / "router")
    mgr = CanvasManager(state_dir=tmp_path / ".aegis" / "state",
                        notifier=make_canvas_notifier(router))
    await mgr.open("r", str(tmp_path / "r.md"))
    mgr.subscribe("r", "bob")        # all sections
    mgr.subscribe("r", "carol", sections=["data"])  # data only
    # alice writes intro → only bob gets notified
    await mgr.write_section("r", "intro", "hi", writer="alice")
    assert len(router.pending("bob")) == 1
    assert len(router.pending("carol")) == 0
    # alice writes data → both bob and carol get notified
    await mgr.write_section("r", "data", "numbers", writer="alice")
    assert len(router.pending("bob")) == 2
    assert len(router.pending("carol")) == 1


@pytest.mark.asyncio
async def test_writer_does_not_self_notify_via_notifier(tmp_path):
    router = InboxRouter(state_dir=tmp_path / "router")
    mgr = CanvasManager(state_dir=tmp_path / ".aegis" / "state",
                        notifier=make_canvas_notifier(router))
    await mgr.open("r", str(tmp_path / "r.md"))
    mgr.subscribe("r", "alice")
    await mgr.write_section("r", "intro", "hi", writer="alice")
    assert router.pending("alice") == []


@pytest.mark.asyncio
async def test_append_notification_shows_appended_text_only(tmp_path):
    router = InboxRouter(state_dir=tmp_path / "router")
    mgr = CanvasManager(state_dir=tmp_path / ".aegis" / "state",
                        notifier=make_canvas_notifier(router))
    await mgr.open("r", str(tmp_path / "r.md"))
    mgr.subscribe("r", "bob")
    await mgr.write_section("r", "log", "existing line", writer="alice")
    await mgr.append_to_section("r", "log", "fresh line", writer="alice")
    notifs = router.pending("bob")
    assert len(notifs) == 2
    assert "appended" in notifs[1].body
    assert "fresh line" in notifs[1].body
    # The "existing line" body should NOT appear in the append preview
    second_lines = notifs[1].body.splitlines()
    preview_idx = second_lines.index("──") + 1
    preview = "\n".join(second_lines[preview_idx:])
    assert "existing line" not in preview
