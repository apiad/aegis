"""MonitorManager: poll semantics, terminal callbacks, interrupt-if-working."""
from __future__ import annotations

import pytest

from aegis.monitor.manager import MonitorManager
from aegis.monitor.schema import DONE, FAILED, WATCHING
from aegis.queue.inbox import InboxRouter


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeBash:
    """Maps a command string to a fixed ``(exit_code, stdout)``.

    Unmapped commands default to ``(1, "")`` — i.e. "not done yet".
    """

    def __init__(self, mapping: dict[str, tuple[int, str]]) -> None:
        self._map = mapping
        self.calls: list[str] = []

    async def __call__(self, cmd: str, cwd) -> tuple[int, str]:
        self.calls.append(cmd)
        return self._map.get(cmd, (1, ""))


class StubSessionManager:
    def __init__(self, sessions=()) -> None:
        self._sessions = list(sessions)
        self.interrupted: list[str] = []

    def list_sessions(self):
        return self._sessions

    async def interrupt(self, handle: str) -> None:
        self.interrupted.append(handle)


class _Info:
    def __init__(self, handle, state, unsolicited=False):
        self.handle, self.state = handle, state
        self.unsolicited = unsolicited


def _mm(mapping, sm=None, clock=None):
    return MonitorManager(
        InboxRouter(), sm, run_bash=FakeBash(mapping),
        clock=clock or FakeClock(), now=lambda: "2026-07-20T00:00:00Z")


async def _inbox_for(mm, handle):
    # InboxRouter buffers to _pending when no session is bound.
    return mm._inbox._pending.get(handle, [])


@pytest.mark.asyncio
async def test_done_delivers_ok_callback():
    mm = _mm({"chk-done": (0, "")})
    mid = mm.start_monitor(from_handle="p", description="pytest",
                           done="chk-done", autorun=False)
    await mm.tick(mid)
    assert mm.status(mid)["state"] == DONE
    pending = await _inbox_for(mm, "p")
    assert len(pending) == 1
    assert pending[0].status == "ok"
    assert "pytest" in pending[0].body and "✓ done" in pending[0].body
    assert pending[0].task_id == mid


@pytest.mark.asyncio
async def test_not_done_keeps_watching_no_delivery():
    mm = _mm({})  # done → (1,"") = not yet
    mid = mm.start_monitor(from_handle="p", description="build",
                           done="chk", autorun=False)
    await mm.tick(mid)
    assert mm.status(mid)["state"] == WATCHING
    assert await _inbox_for(mm, "p") == []
    # Live monitor shows on the strip snapshot.
    assert [v.id for v in mm.snapshot()] == [mid]


@pytest.mark.asyncio
async def test_fail_takes_precedence_over_done():
    mm = _mm({"boom": (0, ""), "chk-done": (0, "")})
    mid = mm.start_monitor(from_handle="p", description="deploy",
                           done="chk-done", fail="boom", autorun=False)
    await mm.tick(mid)
    assert mm.status(mid)["state"] == FAILED
    pending = await _inbox_for(mm, "p")
    assert pending[0].status == "error"
    assert "✗ failed" in pending[0].body


@pytest.mark.asyncio
async def test_progress_updates_pct_and_eta():
    clock = FakeClock()
    mm = _mm({"prog": (0, "40")}, clock=clock)
    mid = mm.start_monitor(from_handle="p", description="dl", done="chk",
                           progress="prog", autorun=False)
    clock.t = 10.0
    await mm.tick(mid)
    s = mm.status(mid)
    assert s["pct"] == 40.0
    # 40% in 10s → 60% left at the same rate → 15s.
    assert s["eta_s"] == pytest.approx(15.0)


@pytest.mark.asyncio
async def test_timeout_is_terminal():
    clock = FakeClock()
    mm = _mm({}, clock=clock)
    mid = mm.start_monitor(from_handle="p", description="server",
                           done="never", timeout_s=30.0, autorun=False)
    clock.t = 31.0
    await mm.tick(mid)
    assert mm.status(mid)["state"] == "timed_out"
    assert (await _inbox_for(mm, "p"))[0].status == "error"


@pytest.mark.asyncio
async def test_cancel_does_not_notify_agent():
    mm = _mm({})
    mid = mm.start_monitor(from_handle="p", description="x", done="chk",
                           autorun=False)
    res = await mm.cancel(mid)
    assert res["state"] == "cancelled"
    assert await _inbox_for(mm, "p") == []
    assert mm.snapshot() == []


@pytest.mark.asyncio
async def test_interrupts_busy_agent_before_delivery():
    sm = StubSessionManager([_Info("p", "working")])
    mm = _mm({"chk-done": (0, "")}, sm=sm)
    mid = mm.start_monitor(from_handle="p", description="t", done="chk-done",
                           autorun=False)
    await mm.tick(mid)
    assert sm.interrupted == ["p"]


@pytest.mark.asyncio
async def test_unsolicited_turn_not_interrupted():
    # "working" here is a Claude-native unsolicited-turn drain (the harness
    # processing its OWN background-task notification), not a real agent turn.
    # Interrupting it cuts CC mid-resume and wedges the wake behind an extra
    # replay cycle — so the monitor must only deliver (queue), never interrupt.
    sm = StubSessionManager([_Info("p", "working", unsolicited=True)])
    mm = _mm({"chk-done": (0, "")}, sm=sm)
    mid = mm.start_monitor(from_handle="p", description="t", done="chk-done",
                           autorun=False)
    await mm.tick(mid)
    assert sm.interrupted == []
    # Still delivered — it lands as a queued follow-up turn at turn-end.
    assert len(await _inbox_for(mm, "p")) == 1


@pytest.mark.asyncio
async def test_idle_agent_not_interrupted():
    sm = StubSessionManager([_Info("p", "ready")])
    mm = _mm({"chk-done": (0, "")}, sm=sm)
    mid = mm.start_monitor(from_handle="p", description="t", done="chk-done",
                           autorun=False)
    await mm.tick(mid)
    assert sm.interrupted == []
    # Still delivered (wake-on-idle path).
    assert len(await _inbox_for(mm, "p")) == 1


@pytest.mark.asyncio
async def test_real_subprocess_bash_runner():
    """Default run_bash actually shells out — exit code + stdout round-trip."""
    from aegis.monitor.manager import _default_run_bash
    code, out = await _default_run_bash("echo 73", None)
    assert code == 0 and out.strip() == "73"
    code, _ = await _default_run_bash("exit 3", None)
    assert code == 3


@pytest.mark.asyncio
async def test_real_done_condition_end_to_end(tmp_path):
    """A file-existence `done` condition trips once the file appears."""
    from aegis.monitor.manager import MonitorManager
    marker = tmp_path / "done.flag"
    mm = MonitorManager(InboxRouter(), now=lambda: "t")
    mid = mm.start_monitor(from_handle="p", description="job",
                           done=f"test -f {marker}", cwd=str(tmp_path),
                           autorun=False)
    await mm.tick(mid)
    assert mm.status(mid)["state"] == WATCHING   # file not there yet
    marker.write_text("x")
    await mm.tick(mid)
    assert mm.status(mid)["state"] == DONE


@pytest.mark.asyncio
async def test_reap_cancels_live_monitors_for_handle():
    mm = _mm({})
    mid = mm.start_monitor(from_handle="p", description="x", done="chk",
                           autorun=False)
    mm.reap("p")
    assert mm.status(mid)["state"] == "cancelled"
    assert mm.snapshot() == []
