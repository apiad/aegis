from __future__ import annotations

import time

from aegis.queue.schema import (
    Delivery,
    InboxMessage,
    Queue,
    Task,
    new_ulid,
    now_iso,
    render_inbox_header,
    sender_agent,
    sender_queue,
    sender_user,
)


def test_sender_tag_helpers():
    assert sender_queue("impl") == "queue:impl"
    assert sender_agent("lucid-knuth") == "agent:lucid-knuth"


def test_new_ulid_is_26_crockford_chars_and_sorts():
    a = new_ulid()
    # 1ms tick guarantees chronological ordering (random tail can otherwise
    # flake when both ULIDs land in the same ms).
    time.sleep(0.002)
    b = new_ulid()
    assert len(a) == 26 and len(b) == 26
    allowed = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert set(a) <= allowed and set(b) <= allowed
    assert b >= a


def test_now_iso_shape():
    s = now_iso()
    # Y-m-dTH:M:SZ — 20 chars, ends with Z
    assert len(s) == 20 and s.endswith("Z") and s[10] == "T"


def test_dataclasses_construct():
    q = Queue(name="impl", agent_profile="claude-impl", max_parallel=2)
    assert q.name == "impl"
    t = Task(id="01J42", queue="impl", payload="do thing",
             enqueued_by="agent:lucid-knuth", enqueued_at="2026-05-20T07:14:00Z",
             callback=True, status="pending")
    assert t.worker_handle is None and t.callback is True
    m = InboxMessage(sender="queue:impl", timestamp="2026-05-20T07:14:00Z",
                     body="done", task_id="01J42", status="ok")
    assert m.task_id == "01J42"


def test_render_inbox_header_with_task():
    m = InboxMessage(sender="queue:impl",
                     timestamp="2026-05-20T07:14:00Z",
                     body="ignored", task_id="01J42", status="ok")
    assert render_inbox_header(m) == (
        "> from queue:impl · task#01J42 · ok · 2026-05-20T07:14:00Z")


def test_render_inbox_header_without_task():
    m = InboxMessage(sender="agent:wry-hopper",
                     timestamp="2026-05-20T07:14:00Z",
                     body="ignored")
    assert render_inbox_header(m) == (
        "> from agent:wry-hopper · 2026-05-20T07:14:00Z")


def test_sender_user_helper():
    assert sender_user() == "user"


def test_user_messages_render_headerless():
    """A text-box message reaches the agent as a plain user turn — no
    substrate '> from …' header."""
    m = InboxMessage(sender=sender_user(),
                     timestamp="2026-05-20T07:14:00Z",
                     body="hello")
    assert render_inbox_header(m) == ""


def test_delivery_receipt():
    landed = Delivery(disposition="landed", depth=0)
    assert landed.disposition == "landed" and landed.depth == 0
    queued = Delivery(disposition="queued", depth=3)
    assert queued.disposition == "queued" and queued.depth == 3
