"""A parked session is a real session, and IdleReaper reaps the daemon
only after a contiguous run of zero views AND zero sessions
(daemon/lifecycle.py:170). So one forgotten parked worker pins the daemon
open forever and sits in the tab bar. The deadline is what keeps a week of
queue work from becoming a row of dead tabs.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
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


async def test_a_close_that_fails_is_logged_not_swallowed(parked_rig):
    """The reaper never revisits this task — it is `failed` and in `reaped` —
    so a close that failed leaves a session standing for the life of the
    process, and `IdleReaper` will not reap a daemon while any session
    stands. That is the exact bug the deadline exists to remove, made
    permanent. Suppressed, it is also invisible.

    The handler goes on the manager's own logger rather than through caplog,
    for the reason `test_session_generation_config` writes down:
    `aegis_log.open()` sets `propagate = False` on the "aegis" logger and
    never restores it, so after any earlier test has opened the log nothing
    from here reaches the root handler caplog listens on.
    """
    qm, sm, tid, handle, parked_at = parked_rig
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    async def boom(_h):
        raise RuntimeError("the socket went away")

    sm.close = boom
    log = logging.getLogger("aegis.queue.manager")
    keep = _Keep(level=logging.WARNING)
    log.addHandler(keep)
    try:
        assert await qm.reap_parked(parked_at + 86401) == [tid]
    finally:
        log.removeHandler(keep)

    assert qm.status(tid)["status"] == "failed", "the task still goes terminal"
    assert any(
        handle in r.getMessage() and tid in r.getMessage()
        for r in records
        if r.levelno >= logging.WARNING
    ), "nothing said the session is still standing"


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


async def test_the_reaper_sweeps_before_its_first_sleep(parked_rig):
    """A daemon that boots, replays a long-parked task and dies inside the
    interval must still have looked.

    With the sleep first, the only sweep a five-minute interval ever gets is
    five minutes in — so on every boot of a daemon that does not live that
    long, `recoverable_ttl_s` is a deadline nothing checks. `interval_s` here
    is longer than the test could ever wait, which is the point: the call has
    to arrive without it elapsing.
    """
    qm, sm, tid, handle, parked_at = parked_rig
    stop = asyncio.Event()
    swept = asyncio.Event()
    real = qm.reap_parked

    async def spy(now):
        swept.set()
        return await real(now)

    qm.reap_parked = spy
    task = asyncio.create_task(ParkReaper(qm, stop=stop, interval_s=3600).run())
    try:
        await asyncio.wait_for(swept.wait(), 1)
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_the_standalone_tui_arms_the_reaper_too(pane_app):
    """`_serve` is not the only brain. An unbridged `AegisApp` builds its
    own `QueueManager`, and `/queues new` hot-registers a queue into that
    live manager — so a worker parks in a standalone TUI, and with no
    reaper on a clock `recoverable_ttl_s` is a deadline nothing ever
    checks. The parked session then stands for the life of the process,
    which is the forgotten-worker bug the deadline exists to stop.
    """
    async with pane_app() as (_pane, pilot):
        app = pilot.app
        assert "park-reaper" in {w.group for w in app.workers}, (
            "the standalone TUI booted with no reaper on its own queue manager"
        )


async def test_the_tui_reaper_is_told_to_stop_when_the_app_exits(pane_app):
    """Its loop sleeps five minutes between sweeps, so an app that exits by
    any path other than `action_quit` would leave it sleeping on a manager
    nothing else holds. `on_unmount` is the path every shutdown takes."""
    async with pane_app() as (_pane, pilot):
        app = pilot.app
        assert not app._park_reaper_stop.is_set()
    assert app._park_reaper_stop.is_set()
