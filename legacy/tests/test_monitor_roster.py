"""The anti-stale roster: an agent's OTHER live monitors, surfaced at the three
moments it is already thinking about monitors — arming one, being woken by one
finishing, and cancelling one.

Motivating burn (2026-08-10, session ``une-tools-release``): the agent killed a
chained pytest by PID, which also killed the ``bash`` waiting to write the
marker file. Its monitor kept watching for a ``/tmp/b2.txt`` that could never
arrive. The agent then armed two MORE monitors and was woken by one of them,
never noticing the orphan — Alex had to point it out twice from outside.
"""
from __future__ import annotations

import pytest

from aegis.monitor.manager import MonitorManager
from aegis.monitor.schema import format_elapsed, roster_block
from aegis.queue.inbox import InboxRouter


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _mm(mapping, clock=None):
    async def run_bash(cmd, cwd):
        return mapping.get(cmd, (1, ""))
    return MonitorManager(InboxRouter(), run_bash=run_bash,
                          clock=clock or FakeClock(),
                          now=lambda: "2026-08-10T00:00:00Z")


async def _inbox_for(mm, handle):
    return mm._inbox._pending.get(handle, [])


# ----- MonitorManager.roster ----------------------------------------------

@pytest.mark.asyncio
async def test_roster_is_scoped_to_the_asking_handle():
    mm = _mm({})
    mine = mm.start_monitor(from_handle="p", description="mine", done="c",
                            autorun=False)
    mm.start_monitor(from_handle="other", description="theirs", done="c",
                     autorun=False)
    assert [r["id"] for r in mm.roster("p")] == [mine]


@pytest.mark.asyncio
async def test_roster_excludes_the_named_monitor():
    mm = _mm({})
    a = mm.start_monitor(from_handle="p", description="a", done="c",
                         autorun=False)
    b = mm.start_monitor(from_handle="p", description="b", done="c",
                         autorun=False)
    assert [r["id"] for r in mm.roster("p", exclude=b)] == [a]


@pytest.mark.asyncio
async def test_roster_drops_terminal_monitors():
    mm = _mm({"done-now": (0, "")})
    finished = mm.start_monitor(from_handle="p", description="a",
                                done="done-now", autorun=False)
    mm.start_monitor(from_handle="p", description="b", done="never",
                     autorun=False)
    await mm.tick(finished)
    assert [r["description"] for r in mm.roster("p")] == ["b"]


@pytest.mark.asyncio
async def test_roster_carries_pct_and_elapsed():
    clock = FakeClock()
    mm = _mm({"prog": (0, "60")}, clock=clock)
    mid = mm.start_monitor(from_handle="p", description="suite", done="never",
                           progress="prog", autorun=False)
    clock.t = 754.0
    await mm.tick(mid)
    (row,) = mm.roster("p")
    assert row["pct"] == 60.0
    assert row["elapsed_s"] == 754
    assert row["description"] == "suite"


# ----- the terminal callback ----------------------------------------------

@pytest.mark.asyncio
async def test_terminal_callback_lists_the_other_live_monitors():
    """The wake that would have caught the orphan."""
    clock = FakeClock()
    mm = _mm({"done-now": (0, "")}, clock=clock)
    orphan = mm.start_monitor(from_handle="p", done="never", autorun=False,
                              description="suite limpia (huérfana)")
    ending = mm.start_monitor(from_handle="p", description="suite definitiva",
                              done="done-now", autorun=False)
    clock.t = 413.0
    await mm.tick(ending)

    (msg,) = await _inbox_for(mm, "p")
    assert "suite definitiva — ✓ done (413s)" in msg.body
    assert "Still watching (1)" in msg.body
    assert orphan in msg.body                      # cancellable as printed
    assert "suite limpia (huérfana)" in msg.body
    assert "aegis_monitor_cancel" in msg.body
    # The monitor that just fired is not in its own roster.
    assert msg.body.count(ending) == 0


@pytest.mark.asyncio
async def test_terminal_callback_stays_quiet_when_nothing_else_is_live():
    mm = _mm({"done-now": (0, "")})
    mid = mm.start_monitor(from_handle="p", description="solo",
                           done="done-now", autorun=False)
    await mm.tick(mid)
    (msg,) = await _inbox_for(mm, "p")
    assert "Still watching" not in msg.body
    assert msg.body.strip() == "solo — ✓ done (0s)"


@pytest.mark.asyncio
async def test_terminal_callback_ignores_a_peers_monitors():
    mm = _mm({"done-now": (0, "")})
    mm.start_monitor(from_handle="peer", description="not mine", done="never",
                     autorun=False)
    mid = mm.start_monitor(from_handle="p", description="mine",
                           done="done-now", autorun=False)
    await mm.tick(mid)
    (msg,) = await _inbox_for(mm, "p")
    assert "not mine" not in msg.body


@pytest.mark.asyncio
async def test_a_failed_monitor_also_carries_the_roster():
    mm = _mm({"boom": (0, "")})
    mm.start_monitor(from_handle="p", description="still going", done="never",
                     autorun=False)
    mid = mm.start_monitor(from_handle="p", description="deploy", done="never",
                           fail="boom", autorun=False)
    await mm.tick(mid)
    (msg,) = await _inbox_for(mm, "p")
    assert msg.status == "error"
    assert "✗ failed" in msg.body and "still going" in msg.body


@pytest.mark.asyncio
async def test_cancel_delivers_no_inbox_wake():
    """Deliberate — the acknowledgement is the tool result, not a turn."""
    mm = _mm({})
    mm.start_monitor(from_handle="p", description="other", done="never",
                     autorun=False)
    mid = mm.start_monitor(from_handle="p", description="x", done="never",
                           autorun=False)
    await mm.cancel(mid)
    assert await _inbox_for(mm, "p") == []


# ----- formatting ----------------------------------------------------------

def test_format_elapsed_reads_at_a_glance():
    assert format_elapsed(0) == "0s"
    assert format_elapsed(45) == "45s"
    assert format_elapsed(59) == "59s"
    assert format_elapsed(60) == "1m"
    assert format_elapsed(754) == "12m"
    assert format_elapsed(3600) == "1h0m"
    assert format_elapsed(3783) == "1h3m"


def test_roster_block_is_empty_for_no_rows():
    assert roster_block([]) == ""


def test_roster_block_omits_pct_when_there_is_none():
    block = roster_block([
        {"id": "01ABC", "description": "no progress arg", "pct": None,
         "elapsed_s": 90}])
    assert "%" not in block
    assert "1m" in block


def test_roster_block_shows_pct_when_present():
    block = roster_block([
        {"id": "01ABC", "description": "suite", "pct": 60.0,
         "elapsed_s": 754}])
    assert "60%" in block and "12m" in block and "01ABC" in block


# ----- cancel: confirm what died, and what is still alive ------------------

@pytest.mark.asyncio
async def test_cancel_names_what_it_cancelled():
    """`{ok: true}` alone makes the agent take on faith that it hit the one
    it meant — with ULIDs that differ in four characters, that is a bad bet."""
    mm = _mm({})
    mid = mm.start_monitor(from_handle="p", done="never", autorun=False,
                           description="suite limpia (huérfana)")
    res = await mm.cancel(mid)
    assert res["ok"] is True
    assert res["state"] == "cancelled"
    assert res["description"] == "suite limpia (huérfana)"


@pytest.mark.asyncio
async def test_cancel_reports_what_is_still_watching():
    """Cancelling is when an agent is pruning, so it is the best moment to
    show the rest of the pile."""
    clock = FakeClock()
    mm = _mm({"prog60": (0, "60")}, clock=clock)
    keep = mm.start_monitor(from_handle="p", description="diagnóstico",
                            done="never", progress="prog60", autorun=False)
    drop = mm.start_monitor(from_handle="p", description="huérfana",
                            done="never", autorun=False)
    await mm.tick(keep)
    clock.t = 754.0
    res = await mm.cancel(drop)
    assert [r["id"] for r in res["still_watching"]] == [keep]
    assert res["still_watching"][0]["pct"] == 60.0
    assert res["still_watching"][0]["elapsed_s"] == 754
    assert "1" in res["note"]


@pytest.mark.asyncio
async def test_cancelling_the_last_one_says_so_plainly():
    mm = _mm({})
    mid = mm.start_monitor(from_handle="p", description="x", done="never",
                           autorun=False)
    res = await mm.cancel(mid)
    assert res["still_watching"] == []
    assert "no monitors" in res["note"]


@pytest.mark.asyncio
async def test_cancel_does_not_count_a_peers_monitors():
    mm = _mm({})
    mm.start_monitor(from_handle="peer", description="theirs", done="never",
                     autorun=False)
    mid = mm.start_monitor(from_handle="p", description="mine", done="never",
                           autorun=False)
    res = await mm.cancel(mid)
    assert res["still_watching"] == []


@pytest.mark.asyncio
async def test_cancelling_an_already_terminal_monitor_still_names_it():
    mm = _mm({"done-now": (0, "")})
    mid = mm.start_monitor(from_handle="p", description="ya terminó",
                           done="done-now", autorun=False)
    await mm.tick(mid)
    res = await mm.cancel(mid)
    assert res["state"] == "done"
    assert res["description"] == "ya terminó"
    assert "already terminal" in res["note"]


@pytest.mark.asyncio
async def test_cancelling_an_unknown_id_is_still_an_error():
    mm = _mm({})
    res = await mm.cancel("01NOPE")
    assert res["ok"] is False and "unknown monitor" in res["error"]
