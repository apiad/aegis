"""A callback carries what the worker actually said.

The task result IS the worker's final assistant text — that is the
contract `aegis_enqueue` sells ("phrase the payload so the worker's
natural final answer is the thing you want back"). `_finalize` honours
it on both the success and the error path.

Two other paths end a worker, and both used to throw its words away:

- `cancel()` sent the producer the literal string "cancelled". A worker
  that had done twenty minutes of work and said so was reduced to one
  word, and the producer had no way to know anything had happened.
- `_mark_interrupted()` (boot replay after a crash) sent the canned
  restart notice. Nothing of the worker survived the process.

And a worker that ends having emitted no assistant text at all produced
an *empty* callback body — indistinguishable, in an inbox, from a
message that failed to render.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.events import AssistantText
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent

from tests.test_queue_manager import StubSessionManager


SAID = ("Reproduced the deadlock: beaver's Transaction holds the write "
        "lock across the whole /healthz probe. Fix is in storage.py.")


class SpeaksThenHangs:
    """Says one thing, then never yields again — a worker mid-task."""

    def __init__(self, text=SAID):
        self._text = text
        self.started = self.closed = False
        self.sent: list[str] = []

    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True

    async def events(self):
        yield AssistantText(text=self._text)
        await asyncio.Event().wait()


class SilentThenHangs(SpeaksThenHangs):
    async def events(self):
        await asyncio.Event().wait()
        if False:  # pragma: no cover — keeps it a generator
            yield


class SM(StubSessionManager):
    def __init__(self, harness):
        super().__init__()
        self._harness = harness

    def spawn(self, slug, *, opening_prompt=None, handle=None):
        from aegis.core.session import AgentSession
        s = AgentSession(self._harness, agent=None, agent_slug=slug,
                         handle=handle)
        self._sessions.append(s)
        self.spawns.append((slug, handle, opening_prompt, s))
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s


def _q(cap=2):
    return Queue(name="impl", agent_profile="claude-impl", max_parallel=cap)


async def _inflight(harness):
    sm = SM(harness)
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox,
                      handle_factory=lambda used: "w1")
    tid, _ = qm.enqueue("impl", "fix the deadlock",
                        enqueued_by=sender_agent("producer"), callback=True)
    for _ in range(60):
        await asyncio.sleep(0)
    return qm, inbox, tid


# ---------- the last MESSAGE, not the last chunk ------------------------

async def _result_of(events):
    sm = StubSessionManager()
    sm.script("w1", events)
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox,
                      handle_factory=lambda used: "w1")
    tid, _ = qm.enqueue("impl", "go", enqueued_by=sender_agent("producer"),
                        callback=True)
    for _ in range(80):
        await asyncio.sleep(0)
    return qm.status(tid)["result"], inbox.pending("producer")[-1].body


def _done():
    from aegis.events import Result
    return Result(duration_ms=1, is_error=False, usage=None)


@pytest.mark.asyncio
async def test_a_streamed_final_message_arrives_whole():
    """Assistant text arrives as a token stream — `coalesce_chunks` exists
    because one message is 116 events. Capturing "the last AssistantText"
    captured the last *chunk*, so a worker that ended with "Fixed the
    deadlock in storage.py; suite is green." reported back "green.".
    """
    result, body = await _result_of([
        AssistantText(text="Fixed the deadlock in ", message_id="m1"),
        AssistantText(text="storage.py; suite is ", message_id="m1"),
        AssistantText(text="green.", message_id="m1"),
        _done()])
    assert result == "Fixed the deadlock in storage.py; suite is green."
    assert result in body


@pytest.mark.asyncio
async def test_a_new_message_replaces_the_previous_one():
    """Accumulation is within a message, not across the conversation —
    otherwise the result is the worker's entire monologue."""
    result, _ = await _result_of([
        AssistantText(text="Looking into it.", message_id="m1"),
        AssistantText(text="Found it: ", message_id="m2"),
        AssistantText(text="a lock ordering bug.", message_id="m2"),
        _done()])
    assert result == "Found it: a lock ordering bug."


@pytest.mark.asyncio
async def test_chunks_with_no_message_id_still_coalesce():
    """The pre-slice-2 claude case, which `coalesce_chunks` handles by
    grouping adjacent same-kind events."""
    result, _ = await _result_of([
        AssistantText(text="suite green; "),
        AssistantText(text="committed as abc1234."),
        _done()])
    assert result == "suite green; committed as abc1234."


@pytest.mark.asyncio
async def test_an_intervening_event_breaks_the_run():
    """Same rule `coalesce_chunks` uses: any non-chunk event ends the
    message. Without it, id-less drivers concatenate forever."""
    from aegis.events import ToolResult
    result, _ = await _result_of([
        AssistantText(text="let me check the log"),
        ToolResult(text="...", tool_call_id="c1", is_error=False),
        AssistantText(text="confirmed, it deadlocks on write."),
        _done()])
    assert result == "confirmed, it deadlocks on write."


@pytest.mark.asyncio
async def test_a_subagents_narration_is_not_the_workers_answer():
    """`capture_next_reply` says it outright — "a peer that runs a `Task`
    must not fold its subagent's commentary into the answer the operator
    reads" — and the queue is the one capture path that never checked.

    A worker that dispatches a subagent gets its chatter interleaved with
    its own text, so the producer's callback can be the subagent talking.
    """
    result, _ = await _result_of([
        AssistantText(text="Done: the deadlock is in storage.py.",
                      message_id="m2"),
        # A Task's narration lands after the worker's own sign-off — the
        # subagent is still streaming when the parent turn wraps up.
        AssistantText(text="I looked at 40 files and found nothing",
                      message_id="sub", parent_tool_use_id="t1"),
        _done()])
    assert result == "Done: the deadlock is in storage.py."


@pytest.mark.asyncio
async def test_a_subagent_chunk_does_not_break_the_workers_own_run():
    """It is skipped, not treated as an intervening event — otherwise a
    subagent speaking mid-stream truncates the worker's own message."""
    result, _ = await _result_of([
        AssistantText(text="Fixed the deadlock in ", message_id="m1"),
        AssistantText(text="(subagent noise)", message_id="sub",
                      parent_tool_use_id="t1"),
        AssistantText(text="storage.py.", message_id="m1"),
        _done()])
    assert result == "Fixed the deadlock in storage.py."


# ---------- cancel ------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_callback_carries_the_workers_last_message():
    qm, inbox, tid = await _inflight(SpeaksThenHangs())
    await qm.cancel(tid)
    body = inbox.pending("producer")[-1].body
    assert "beaver's Transaction holds the write lock" in body


@pytest.mark.asyncio
async def test_cancel_callback_still_says_it_was_cancelled():
    """The last message must not displace the outcome — a producer
    reading only the body has to know the task did not finish."""
    qm, inbox, tid = await _inflight(SpeaksThenHangs())
    await qm.cancel(tid)
    msg = inbox.pending("producer")[-1]
    assert "cancelled" in msg.body.lower()
    assert msg.status == "error"


@pytest.mark.asyncio
async def test_cancel_records_the_last_message_as_the_task_result():
    """So `aegis_task_status` shows it too, not just the one inbox
    message the producer may already have consumed."""
    qm, _, tid = await _inflight(SpeaksThenHangs())
    await qm.cancel(tid)
    assert SAID in (qm.status(tid)["result"] or "")


@pytest.mark.asyncio
async def test_cancelling_a_silent_worker_says_so_rather_than_faking_it():
    qm, inbox, tid = await _inflight(SilentThenHangs())
    await qm.cancel(tid)
    body = inbox.pending("producer")[-1].body
    assert "cancelled" in body.lower()
    assert body.strip(), "an empty callback body reads as a broken message"


@pytest.mark.asyncio
async def test_cancelling_a_pending_task_has_no_worker_to_quote():
    """Never dispatched, so there is no last message and nothing to
    imply there was one."""
    sm = SM(SpeaksThenHangs())
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q(cap=1)}, sm, inbox,
                      handle_factory=lambda used: f"w{len(used)}")
    qm.enqueue("impl", "first", enqueued_by=sender_agent("producer"),
               callback=True)
    tid2, _ = qm.enqueue("impl", "second",
                         enqueued_by=sender_agent("producer"), callback=True)
    for _ in range(60):
        await asyncio.sleep(0)
    assert qm.status(tid2)["status"] == "pending"
    await qm.cancel(tid2)
    body = inbox.pending("producer")[-1].body
    assert "cancelled" in body.lower()
    assert SAID not in body


# ---------- an empty final message --------------------------------------

@pytest.mark.asyncio
async def test_a_worker_that_says_nothing_gets_an_honest_callback():
    """It ended cleanly having emitted no assistant text — tools only.
    An empty body is indistinguishable from a message that failed to
    render, so say what happened instead."""
    from aegis.events import Result
    sm = StubSessionManager()
    sm.script("w1", [Result(duration_ms=1, is_error=False, usage=None)])
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, sm, inbox,
                      handle_factory=lambda used: "w1")
    tid, _ = qm.enqueue("impl", "go", enqueued_by=sender_agent("producer"),
                        callback=True)
    for _ in range(60):
        await asyncio.sleep(0)
    assert qm.status(tid)["status"] == "completed"
    body = inbox.pending("producer")[-1].body
    assert body.strip(), "an empty callback body reads as a broken message"


# ---------- crash replay -------------------------------------------------

@pytest.mark.asyncio
async def test_interrupted_replay_carries_what_the_log_kept(tmp_path):
    """A worker deferred at least once, so its words reached the log
    before the process died. The restart notice carries them."""
    from aegis.queue.jsonl import append_record

    log = tmp_path / "queues" / "impl.jsonl"
    for rec in (
        {"event": "enqueued", "task_id": "t1", "payload": "go",
         "enqueued_by": "agent:producer", "callback": True},
        {"event": "dispatched", "task_id": "t1", "worker_handle": "w1"},
        {"event": "deferred", "task_id": "t1", "worker_handle": "w1",
         "waiting_on": ["1 live monitor(s) still watching"],
         "last_text": SAID},
    ):
        append_record(log, rec)

    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, StubSessionManager(), inbox,
                      state_dir=tmp_path)
    await qm.start()
    body = inbox.pending("producer")[-1].body
    assert "interrupted" in body.lower()
    assert SAID in body
    # And on the task too, for a producer that reads status instead of
    # the one inbox message it may already have consumed.
    assert SAID in (qm.status("t1")["result"] or "")


@pytest.mark.asyncio
async def test_interrupted_replay_with_nothing_kept_claims_nothing(tmp_path):
    """Died before it said anything. The notice must not imply otherwise."""
    from aegis.queue.jsonl import append_record

    log = tmp_path / "queues" / "impl.jsonl"
    for rec in (
        {"event": "enqueued", "task_id": "t1", "payload": "go",
         "enqueued_by": "agent:producer", "callback": True},
        {"event": "dispatched", "task_id": "t1", "worker_handle": "w1"},
    ):
        append_record(log, rec)

    inbox = InboxRouter()
    qm = QueueManager({"impl": _q()}, StubSessionManager(), inbox,
                      state_dir=tmp_path)
    await qm.start()
    body = inbox.pending("producer")[-1].body
    assert "interrupted" in body.lower()
    assert body.strip()
