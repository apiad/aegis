"""A parked session is a real session, and IdleReaper reaps the daemon
only after a contiguous run of zero views AND zero sessions
(daemon/lifecycle.py:170). So one forgotten parked worker pins the daemon
open forever and sits in the tab bar. The deadline is what keeps a week of
queue work from becoming a row of dead tabs.
"""
from __future__ import annotations

import asyncio
import inspect
import time

from aegis import cli
from aegis.queue import ParkReaper
from aegis.queue.jsonl import read_records


async def test_a_parked_session_past_its_ttl_is_closed_and_failed(parked_rig):
    qm, sm, tid, handle, parked_at = parked_rig
    reaped = await qm.reap_parked(parked_at + 86401)
    assert reaped == [tid]
    assert qm.status(tid)["status"] == "failed"
    assert handle in sm.closed


async def test_a_parked_session_inside_its_ttl_is_left_alone(parked_rig):
    qm, sm, tid, handle, parked_at = parked_rig
    assert await qm.reap_parked(parked_at + 3600) == []
    assert qm.status(tid)["status"] == "recoverable"
    assert handle not in sm.closed


async def test_ttl_zero_never_reaps(parked_rig_ttl_zero):
    """For a host where keeping them forever is what you want."""
    qm, sm, tid, handle, parked_at = parked_rig_ttl_zero
    assert await qm.reap_parked(parked_at + 999_999) == []
    assert qm.status(tid)["status"] == "recoverable"
    assert handle not in sm.closed


async def test_the_discard_is_announced_to_the_producer(parked_rig):
    """Bounded, announced loss after a full day is a different thing from
    the silent loss four seconds after a dropped link that this whole
    change exists to remove."""
    qm, sm, tid, handle, parked_at = parked_rig
    await qm.reap_parked(parked_at + 86401)
    body = sm.inbox_for("producer")[-1].body
    assert "discarded" in body and tid in body


async def test_reaping_twice_reaps_nothing_the_second_time(parked_rig):
    """The reaper runs on a timer, so it sees the same tasks again five
    minutes later. A second pass over a task it already failed would
    deliver the producer a duplicate notice and close a handle that is
    already gone."""
    qm, sm, tid, handle, parked_at = parked_rig
    await qm.reap_parked(parked_at + 86401)
    before = len(sm.inbox_for("producer"))

    assert await qm.reap_parked(parked_at + 86402) == []
    assert sm.closed.count(handle) == 1
    assert len(sm.inbox_for("producer")) == before


async def test_the_discard_is_on_disk(parked_rig, tmp_path):
    """A restart must not rehydrate a task the reaper already discarded.
    `replay` reads the log, not memory, so the failure has to be written
    there or the parked task comes back at the next boot."""
    qm, sm, tid, handle, parked_at = parked_rig
    await qm.reap_parked(parked_at + 86401)
    records = read_records(tmp_path / "queues" / "impl.jsonl")
    failed = [r for r in records if r["event"] == "failed" and r["task_id"] == tid]
    assert len(failed) == 1
    assert "discarded" in failed[-1]["error"]


# --- the clock behind the deadline ---------------------------------------
#
# `reap_parked` takes `now`, which is what makes it testable and also what
# makes it inert: nothing happens until something calls it on a timer. The
# tests above would all pass with the reaper never wired up at all.


async def test_the_reaper_calls_reap_parked_and_stops_on_its_event(parked_rig):
    qm, sm, tid, handle, parked_at = parked_rig
    stop = asyncio.Event()
    seen: list[float] = []
    real = qm.reap_parked

    async def spy(now):
        seen.append(now)
        stop.set()
        return await real(now)

    qm.reap_parked = spy
    await asyncio.wait_for(ParkReaper(qm, stop=stop, interval_s=0).run(), 1)

    assert seen, "the reaper never called reap_parked"
    # Wall clock, not a test fixture's epoch: the deadline is real time.
    assert abs(seen[0] - time.time()) < 5
    # And it passed the argument to a live manager, which left this task —
    # parked seconds ago, ttl a day — exactly where it was.
    assert qm.status(tid)["status"] == "recoverable"


async def test_a_raising_reap_does_not_kill_the_reaper(parked_rig):
    """One malformed task must not stop the reaper for the rest of the
    process's life — that is the forgotten-worker bug back, with a
    traceback nobody reads."""
    qm, sm, tid, handle, parked_at = parked_rig
    stop = asyncio.Event()
    calls: list[float] = []

    async def boom(now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("a task the reaper choked on")
        stop.set()
        return []

    qm.reap_parked = boom
    await asyncio.wait_for(ParkReaper(qm, stop=stop, interval_s=0).run(), 1)

    assert len(calls) == 2


def test_serve_arms_the_reaper():
    """The wiring, pinned at the only altitude a test can reach without
    booting a daemon: `_serve` is a 200-line coroutine that opens sockets.
    Deleting the two lines that arm the reaper would leave every test above
    green and the deadline never checked on any real host."""
    src = inspect.getsource(cli._serve)
    assert "ParkReaper(qm, stop=stop)" in src
