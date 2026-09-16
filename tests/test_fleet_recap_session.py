"""The mid-turn recap, driven through a real session.

Everything here spends money in production, so each test pins one of the
ways it must not: nobody watching, too soon, billed to the wrong profile,
outliving its turn or its session, or running twice at once.
"""
import asyncio

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.events import Result
from aegis.recap import FleetRecap, Recap
from aegis.tui.state import AgentState

from tests.brain import make_brain

FLEET = """\
fleet:
  recap: watched
  recap_after_s: 60
  recap_interval_s: 120
"""


class _BlockingHarness:
    """A turn that runs until the test lets it finish."""

    def __init__(self):
        self.finish = asyncio.Event()

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...
    async def interrupt(self): ...

    async def events(self):
        await self.finish.wait()
        yield Result(duration_ms=0, is_error=False)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _session(root, yaml_text=FLEET, roster=None):
    (root / ".aegis.yaml").write_text(yaml_text)
    roster = roster or {"opus": Agent(harness="claude-code", model="opus",
                                      effort="high", permission="auto")}
    harness = _BlockingHarness()
    mgr = make_brain(roster, "opus",
                     make_session=lambda p, u, h, **kw: harness,
                     mcp=None, roots=AegisRoots.for_project(root))
    s = mgr._sync_spawn("opus")
    clock = _Clock()
    s._now = clock
    s._fleet_check_s = 0
    return s, harness, clock


async def _spin(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def _stub_recap(monkeypatch, *, block=None, cost=0.01):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        # Stands in for the whole call, driver included: a cancel while it
        # blocks is a cancel of a started `claude -p`.
        kw["on_driver"]()
        if block is not None:
            await block.wait()
        return Recap(done="landed x", doing="doing y", cost_usd=cost, ok=True)

    monkeypatch.setattr("aegis.core.session.recap_in_flight_for", fake)
    return calls


def _watch(s):
    """A fleet watcher, plus a turn-recap observer that must stay silent."""
    seen, turn_seen = [], []
    cb = lambda _s, r: seen.append(r)  # noqa: E731
    s.add_fleet_watcher(cb)
    s.add_recap_observer(lambda _s, r: turn_seen.append(r))
    return cb, seen, turn_seen


async def _finish(s, harness):
    harness.finish.set()
    for _ in range(200):
        await asyncio.sleep(0)
        if s.state is not AgentState.working:
            break
    await _spin()


async def test_no_watchers_means_no_call_however_long_the_turn(tmp_path, monkeypatch):
    calls = _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    for _ in range(10):
        clock.t += 600
        await _spin()
    assert calls == []
    assert s._fleet_task is None
    await _finish(s, harness)


async def test_a_watched_turn_pays_once_then_waits_the_interval(tmp_path, monkeypatch):
    calls = _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    cb, seen, turn_seen = _watch(s)
    await _spin()
    assert calls == [], "a young turn waits"
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    assert s.fleet_recap is not None and s.fleet_recap.doing == "doing y"
    assert [r.doing for r in seen] == ["doing y"], "the fleet watcher is the channel"
    assert turn_seen == [], "a fleet recap must not reach the transcript's recap channel"
    clock.t += 60
    await _spin()
    assert len(calls) == 1, "the interval holds"
    clock.t += 61
    await _spin()
    assert len(calls) == 2
    assert s.fleet_recap_calls == 2
    assert s.fleet_recap_cost_usd == pytest.approx(0.02)
    s.remove_fleet_watcher(cb)
    await _finish(s, harness)


HAIKU = FLEET + """\
agents:
  opus:
    provider: claude-code
    model: opus
  haiku:
    provider: claude-code
    model: claude-haiku-4-5-20251001
    effort: low
    permission: read
default_agent: opus
text_generation: haiku
"""


async def test_the_mid_turn_recap_bills_to_text_generation_from_the_config_root(
        tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    roster = {
        "opus": Agent(harness="claude-code", model="opus", effort="high", permission="auto"),
        "haiku": Agent(harness="claude-code", model="claude-haiku-4-5-20251001",
                       effort="low", permission="read"),
    }
    s, harness, clock = _session(root, HAIKU, roster)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / ".aegis.yaml").write_text(
        FLEET + "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    monkeypatch.chdir(decoy)
    billed = []

    class _Driver:
        supports_oneshot = True

        async def generate_detailed(self, agent, cwd, schema, *instructions):
            billed.append((agent.model, schema))
            from aegis.drivers.oneshot import Generation
            return Generation(value=FleetRecap(done="a", doing="b"),
                              model="haiku", cost_usd=0.007)

    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: _Driver())
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda state_dir, log_id: type("R", (), {"events": [], "stamps": []})())
    await s.send("go")
    _watch(s)
    clock.t += 61
    for _ in range(10):
        await _spin()
        if billed and s.fleet_recap is not None:
            break
    assert billed == [("claude-haiku-4-5-20251001", FleetRecap)]
    assert s.fleet_recap.doing == "b"
    assert s.fleet_recap_cost_usd == pytest.approx(0.007)
    await _finish(s, harness)


async def test_a_turn_that_ends_cancels_the_running_recap(tmp_path, monkeypatch):
    block = asyncio.Event()
    calls = _stub_recap(monkeypatch, block=block)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    running = s._fleet_recap_task
    assert running is not None and not running.done()
    await _finish(s, harness)
    assert running.cancelled()
    assert s._fleet_recap_task is None
    assert s.fleet_recap_cancelled == 1, "a killed call's cost is unknowable; count it"
    assert s.fleet_recap_calls == 0
    assert s.fleet_recap is None
    # The periodic task stays armed (someone is still watching) but idle:
    # a ready session never qualifies.
    clock.t += 600
    await _spin()
    assert len(calls) == 1
    assert s._fleet_recap_task is None


async def test_removing_the_last_watcher_cancels_the_periodic_task(tmp_path, monkeypatch):
    _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path)
    a, b = (lambda *_: None), (lambda *_: None)
    s.add_fleet_watcher(a)
    task = s._fleet_task
    assert task is not None and not task.done()
    s.add_fleet_watcher(b)
    assert s._fleet_task is task, "a second watcher does not arm a second task"
    s.remove_fleet_watcher(a)
    await _spin()
    assert not task.done()
    s.remove_fleet_watcher(b)
    await _spin()
    assert task.cancelled()
    assert s._fleet_task is None


async def test_closing_the_session_cancels_the_periodic_task(tmp_path, monkeypatch):
    block = asyncio.Event()
    calls = _stub_recap(monkeypatch, block=block)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    task = s._fleet_task
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    running = s._fleet_recap_task
    await s.close()
    await _spin()
    assert task.cancelled()
    assert running.cancelled()
    assert s._fleet_task is None
    s.add_fleet_watcher(lambda *_: None)
    assert s._fleet_task is None, "a closed session never re-arms"


async def test_no_second_call_while_one_is_still_running(tmp_path, monkeypatch):
    block = asyncio.Event()
    calls = _stub_recap(monkeypatch, block=block)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    clock.t += 10_000
    await _spin(50)
    assert len(calls) == 1
    # Released: the interval has long passed since the first START, so one
    # more call is due — one, not a burst for the checks that were skipped.
    block.set()
    await _spin(50)
    assert len(calls) == 2
    assert s.fleet_recap_calls == 2
    await _spin(50)
    assert len(calls) == 2
    await _finish(s, harness)


async def test_fleet_recap_off_in_the_config_means_no_call(tmp_path, monkeypatch):
    calls = _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path, "fleet:\n  recap: \"off\"\n")
    assert s.fleet_config.recap == "off"
    await s.send("go")
    _watch(s)
    for _ in range(5):
        clock.t += 600
        await _spin()
    assert calls == []
    await _finish(s, harness)


async def test_a_turn_that_ends_clears_its_mid_turn_recap(tmp_path, monkeypatch):
    _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    assert s.fleet_recap is not None
    await _finish(s, harness)
    assert s.fleet_recap is None, "an idle card must not show a stale 'now'"


async def test_each_turn_starts_its_own_interval(tmp_path, monkeypatch):
    calls = _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    clock.t += 50
    await _finish(s, harness)
    harness.finish.clear()
    await s.send("again")
    assert s.state is AgentState.working
    clock.t += 61
    await _spin()
    # 111s after turn 1's recap, under the 120s interval: only a reset at
    # the turn boundary lets turn 2's first recap fire at recap_after_s.
    assert len(calls) == 2
    await _finish(s, harness)


async def test_unset_text_generation_never_bills_the_sessions_own_model(
        tmp_path, monkeypatch):
    s, harness, clock = _session(tmp_path)  # FLEET only: no text_generation
    billed = []

    class _Driver:
        supports_oneshot = True

        async def generate_detailed(self, agent, cwd, schema, *instructions):
            billed.append(agent.model)
            from aegis.drivers.oneshot import Generation
            return Generation(value=FleetRecap(done="a", doing="b"),
                              model="opus", cost_usd=0.05)

    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: _Driver())
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda state_dir, log_id: type("R", (), {"events": [], "stamps": []})())
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin(50)
    assert billed == []
    assert s.fleet_recap is None
    assert s.fleet_recap_calls == 0 and s.fleet_recap_cost_usd == 0.0
    assert s.fleet_recap_failed == 1
    await _finish(s, harness)


async def test_only_calls_that_reached_the_driver_count_as_paid(tmp_path, monkeypatch):
    results = [Recap(error="unknown harness: 'x'"),
               Recap(model="haiku", cost_usd=0.01, error="nothing usable")]
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return results[len(calls) - 1]

    monkeypatch.setattr("aegis.core.session.recap_in_flight_for", fake)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    assert s.fleet_recap_calls == 0 and s.fleet_recap_failed == 1
    clock.t += 121
    await _spin()
    assert len(calls) == 2
    assert s.fleet_recap_calls == 1 and s.fleet_recap_failed == 1
    assert s.fleet_recap_cost_usd == pytest.approx(0.01)
    await _finish(s, harness)


async def test_an_idle_session_never_reads_the_config(tmp_path, monkeypatch):
    _stub_recap(monkeypatch)
    s, harness, clock = _session(tmp_path)
    reads = []
    real = s._generation_config
    s._generation_config = lambda: reads.append(1) or real()
    _watch(s)
    for _ in range(5):
        clock.t += 600
        await _spin()
    assert s.state is not AgentState.working
    assert reads == [], "the state check is free; the config read is a stat"
    s._fleet_watchers.clear()
    s._stop_fleet()


# --- the fix wave: refusal logged once, cancels counted only when paid,
# --- and a reconnect clears what the old process was doing


def _keep_warnings():
    """On the session's own logger, not caplog: `aegis_log.open()` leaves
    `propagate = False` on "aegis" (see test_session_generation_config)."""
    import logging

    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("aegis.core.session")
    handler, level = _Keep(logging.WARNING), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    def undo():
        logger.removeHandler(handler)
        logger.setLevel(level)

    return records, undo


async def test_a_refused_recap_is_logged_once_per_session(tmp_path, monkeypatch):
    """No `text_generation:` means the in-flight recap is refused on every
    check. Silent, the operator sees cards with no `now` line and no
    reason; logged per check, it floods the log every two minutes."""
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda state_dir, log_id: type("R", (), {"events": [], "stamps": []})())
    records, undo = _keep_warnings()
    try:
        s, harness, clock = _session(tmp_path)  # FLEET only: no text_generation
        await s.send("go")
        _watch(s)
        for _ in range(3):
            clock.t += 121
            await _spin(50)
        assert s.fleet_recap_failed == 3
        await _finish(s, harness)
    finally:
        undo()
    hits = [r for r in records if "text_generation" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in records]
    assert s.handle in hits[0].getMessage()


async def test_a_cancel_before_the_driver_counts_nowhere(tmp_path, monkeypatch):
    """Killed while the digest is still being built, the call never
    started, so it cost nothing and is not `cancelled` in the band."""
    block = asyncio.Event()
    driven = []

    class _Driver:
        supports_oneshot = True

        async def generate_detailed(self, *a):
            driven.append(a)

    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: _Driver())
    s, harness, clock = _session(tmp_path)

    async def slow_build(**_kw):
        await block.wait()

    monkeypatch.setattr(s.digest, "build", slow_build)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    running = s._fleet_recap_task
    assert running is not None and not running.done()
    await _finish(s, harness)
    assert running.cancelled()
    assert driven == []
    assert s.fleet_recap_cancelled == 0
    assert (s.fleet_recap_calls, s.fleet_recap_failed) == (0, 0)


async def test_a_cancel_inside_the_driver_counts_as_cancelled(tmp_path, monkeypatch):
    """Through the real `recap_in_flight_for`: the driver was entered, a
    `claude -p` may be running, and its price never prints."""
    block = asyncio.Event()
    entered = []

    class _Driver:
        supports_oneshot = True

        async def generate_detailed(self, *a):
            entered.append(a)
            await block.wait()

    monkeypatch.setattr("aegis.drivers.get_driver", lambda harness: _Driver())
    monkeypatch.setattr("aegis.state.session_log.replay_events",
                        lambda state_dir, log_id: type("R", (), {"events": [], "stamps": []})())
    s, harness, clock = _session(tmp_path, HAIKU, {
        "opus": Agent(harness="claude-code", model="opus"),
        "haiku": Agent(harness="claude-code", model="claude-haiku-4-5-20251001"),
    })
    await s.send("go")
    _watch(s)
    clock.t += 61
    for _ in range(10):
        await _spin()
        if entered:
            break
    assert len(entered) == 1
    await _finish(s, harness)
    assert s.fleet_recap_cancelled == 1


async def test_a_reconnect_drops_the_old_processes_recap(tmp_path, monkeypatch):
    """`adopt` swaps the process under a session and sets it ready without
    going through `_emit_state`, so nothing cleared the `now` line or the
    recap still running against the old process."""
    block = asyncio.Event()
    calls = _stub_recap(monkeypatch, block=block)
    s, harness, clock = _session(tmp_path)
    await s.send("go")
    _watch(s)
    clock.t += 61
    await _spin()
    assert len(calls) == 1
    running = s._fleet_recap_task
    s.fleet_recap = Recap(doing="an earlier line", ok=True)
    s.adopt(_BlockingHarness())
    await _spin()
    assert running.cancelled()
    assert s._fleet_recap_task is None
    assert s.fleet_recap is None
