"""NetService — three cadences over one task.

Probes are injected and the clock is fake, so nothing here waits and nothing
touches a socket. The behaviours under test are exactly the ones a live
network would make hard to reproduce: the forced re-trace when egress comes
back, and what survives a failure.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from aegis.net.probe import Reach, Throughput, Trace
from aegis.net.service import NetService, Probes


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@dataclass
class Cfg:
    """Stand-in for the NetworkConfig Task 4 adds — same attribute names."""

    enabled: bool = True
    interval: float = 20.0
    trace_interval: float = 300.0
    speed_interval: float = 0.0
    speed_bytes: int = 1_000_000
    timeout: float = 3.0
    speed_timeout: float = 30.0
    anchors: tuple[tuple[str, int], ...] = (("1.1.1.1", 443),)


@dataclass
class Spy:
    """Scripted probes that record every call."""

    reaches: list = field(default_factory=list)
    traces: list = field(default_factory=list)
    speeds: list = field(default_factory=list)
    reach_calls: int = 0
    trace_calls: int = 0
    speed_calls: int = 0

    def probes(self) -> Probes:
        async def reach(anchors, timeout):
            self.reach_calls += 1
            return self.reaches.pop(0) if self.reaches else Reach(ok=True, rtt_ms=10.0)

        async def trace(timeout):
            self.trace_calls += 1
            return self.traces.pop(0) if self.traces else Trace(ok=True, ip="1.2.3.4")

        async def throughput(nbytes, timeout):
            self.speed_calls += 1
            return (
                self.speeds.pop(0)
                if self.speeds
                else Throughput(ok=True, bytes_per_s=1e5, received=nbytes,
                                asked=nbytes, elapsed_s=10.0)
            )

        return Probes(reach=reach, trace=trace, throughput=throughput)


def _service(clock, spy, cfg=None):
    return NetService(cfg or Cfg(), probes=spy.probes(), clock=clock)


@pytest.mark.asyncio
async def test_the_first_refresh_samples_egress_and_the_exit_ip():
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy)
    await svc.refresh()

    assert svc.state.sampled
    assert svc.state.reach.ok
    assert svc.state.trace.ip == "1.2.3.4"
    assert spy.reach_calls == 1
    assert spy.trace_calls == 1


@pytest.mark.asyncio
async def test_the_exit_ip_is_not_re_read_on_every_tick():
    """It costs 300 bytes and changes almost never; RTT is the cheap one."""
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(cfg.interval)
    await svc.refresh()

    assert spy.reach_calls == 2
    assert spy.trace_calls == 1


@pytest.mark.asyncio
async def test_the_exit_ip_is_re_read_once_its_own_cadence_is_due():
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(cfg.trace_interval)
    await svc.refresh()

    assert spy.trace_calls == 2


@pytest.mark.asyncio
async def test_egress_coming_back_forces_a_re_read_of_the_exit_ip():
    """The whole reason the transition is tracked: an address changes at
    exactly one moment, and it is this one. Polling for it is waste."""
    clock, spy = Clock(), Spy()
    spy.reaches = [
        Reach(ok=True, rtt_ms=10.0),
        Reach(ok=False, error="unreachable"),
        Reach(ok=True, rtt_ms=11.0),
    ]
    spy.traces = [Trace(ok=True, ip="1.2.3.4"), Trace(ok=True, ip="5.6.7.8")]
    svc = _service(clock, spy)

    await svc.refresh()                      # up, traces
    clock.advance(1.0)
    await svc.refresh()                      # down
    clock.advance(1.0)
    await svc.refresh()                      # back up — forces a trace

    assert spy.trace_calls == 2, "the transition did not force a re-read"
    assert svc.state.trace.ip == "5.6.7.8"


@pytest.mark.asyncio
async def test_the_transition_fires_once_not_on_every_later_tick():
    clock, spy = Clock(), Spy()
    spy.reaches = [
        Reach(ok=False, error="unreachable"),
        Reach(ok=True, rtt_ms=10.0),
        Reach(ok=True, rtt_ms=10.0),
    ]
    svc = _service(clock, spy)
    await svc.refresh()
    clock.advance(1.0)
    await svc.refresh()
    before = spy.trace_calls
    clock.advance(1.0)
    await svc.refresh()

    assert spy.trace_calls == before


@pytest.mark.asyncio
async def test_a_failed_reach_does_not_erase_the_last_known_exit_ip():
    """Losing egress says nothing about what the address was, and the row it
    would blank is the one that tells you which network you fell onto."""
    clock, spy = Clock(), Spy()
    spy.reaches = [Reach(ok=True, rtt_ms=10.0), Reach(ok=False, error="unreachable")]
    svc = _service(clock, spy)
    await svc.refresh()
    clock.advance(1.0)
    await svc.refresh()

    assert not svc.state.reach.ok
    assert svc.state.trace.ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_a_failed_trace_does_not_erase_the_last_known_exit_ip():
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    spy.traces = [Trace(ok=True, ip="1.2.3.4"), Trace(ok=False, error="no exit ip")]
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(cfg.trace_interval)
    await svc.refresh()

    assert svc.state.trace.ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_nothing_is_probed_beyond_reach_while_egress_is_down():
    """No point spending a lookup or a megabyte on a link that just failed."""
    clock, spy = Clock(), Spy()
    spy.reaches = [Reach(ok=False, error="unreachable")]
    svc = _service(clock, spy, Cfg(speed_interval=60.0))
    await svc.refresh()

    assert spy.reach_calls == 1
    assert spy.trace_calls == 0
    assert spy.speed_calls == 0


@pytest.mark.asyncio
async def test_the_speed_probe_stays_silent_when_its_interval_is_off():
    """The default. A megabyte on a timer is the part that needs consent."""
    clock, spy = Clock(), Spy()
    cfg = Cfg(speed_interval=0.0)
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(3600.0)
    await svc.refresh()

    assert spy.speed_calls == 0
    assert svc.state.speed is None


@pytest.mark.asyncio
async def test_the_speed_probe_runs_when_an_interval_is_configured():
    clock, spy = Clock(), Spy()
    cfg = Cfg(speed_interval=300.0)
    svc = _service(clock, spy, cfg)
    await svc.refresh()

    assert spy.speed_calls == 1
    assert svc.state.speed.complete


@pytest.mark.asyncio
async def test_force_speed_overrides_the_interval_being_off():
    """What `/net` does: you asked, so the reading is taken."""
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy, Cfg(speed_interval=0.0))
    await svc.refresh(force_speed=True)

    assert spy.speed_calls == 1


@pytest.mark.asyncio
async def test_a_disabled_service_never_starts_its_task():
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy, Cfg(enabled=False))
    svc.start()
    assert not svc.started
    await svc.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent_so_the_ui_tick_can_call_it_every_second():
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy)
    svc.start()
    first = svc._task
    svc.start()
    assert svc._task is first
    await svc.stop()
    assert not svc.started


@pytest.mark.asyncio
async def test_a_probe_that_raises_does_not_escape_refresh():
    """The UI tick must never see an exception from here. The probes are
    written not to raise; this pins the service's own guarantee in case one
    day one of them does."""
    clock = Clock()

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")

    svc = NetService(
        Cfg(), probes=Probes(reach=boom, trace=boom, throughput=boom), clock=clock
    )
    await svc.refresh()
    assert not svc.state.reach.ok
    assert svc.state.reach.error


@pytest.mark.asyncio
async def test_the_speed_probe_is_given_the_speed_timeout():
    """Not `timeout`, which is the handshake's. See the config test for the
    live reading that found this."""
    clock, spy = Clock(), Spy()
    seen: list[float] = []

    async def throughput(nbytes, timeout):
        seen.append(timeout)
        return Throughput(ok=True, bytes_per_s=1e5, received=nbytes,
                          asked=nbytes, elapsed_s=10.0)

    cfg = Cfg(speed_interval=60.0, timeout=3.0, speed_timeout=30.0)
    probes = spy.probes()._replace(throughput=throughput)
    svc = NetService(cfg, probes=probes, clock=clock)
    await svc.refresh()

    assert seen == [cfg.speed_timeout]


@pytest.mark.asyncio
async def test_a_hermetic_test_never_reaches_the_real_probes(monkeypatch):
    """The suite must not open sockets to 1.1.1.1 or speed.cloudflare.com.

    Every AegisApp builds a NetService and `_tick` starts it, so without a
    guard every TUI test probes the real network on every tick. This is worse
    than the quota poller `no_real_provider_accounts` already covers: that one
    is disarmed by removing credentials, and these probes need none, so they
    would reach out on CI too.

    Spies on the probe module rather than comparing identities — `default_probes`
    wraps each call in a lambda, so `is not probe.reach` is true whether or not
    a guard exists, and an earlier version of this test passed for that reason
    and checked nothing. With the conftest fixture removed, the lambda calls
    `probe.reach` and the spy records it.
    """
    import aegis.net.probe as probe
    from aegis.net.service import NetService

    called: list[str] = []

    async def spy_reach(*a, **kw):
        called.append("reach")
        return Reach(ok=False, error="spy")

    async def spy_trace(*a, **kw):
        called.append("trace")
        return Trace(ok=False, error="spy")

    async def spy_speed(*a, **kw):
        called.append("throughput")
        return Throughput(ok=False, error="spy")

    monkeypatch.setattr(probe, "reach", spy_reach)
    monkeypatch.setattr(probe, "trace", spy_trace)
    monkeypatch.setattr(probe, "throughput", spy_speed)

    # No `probes=`: exactly how AegisApp builds it.
    svc = NetService(Cfg(speed_interval=1.0))
    await svc.refresh(force_speed=True)

    assert not called, f"the service reached the real probe module: {called}"


@pytest.mark.asyncio
async def test_a_slow_refresh_does_not_clobber_a_newer_reading():
    """C1. `/net` awaits refresh(force_speed=True) for up to speed_timeout while
    `_loop` keeps refreshing on its own cadence, and refresh() reads _state,
    awaits, then blind-writes. Unserialised, the slow caller stamps its
    pre-outage snapshot over the live one and the sidebar goes green on a dead
    link.
    """
    clock = Clock()
    release = asyncio.Event()
    seen = {"n": 0}

    async def reach(anchors, timeout):
        seen["n"] += 1
        # First sample is healthy; every later one sees the link gone.
        return Reach(ok=True, rtt_ms=10.0) if seen["n"] == 1 else Reach(
            ok=False, error="unreachable"
        )

    async def trace(timeout):
        return Trace(ok=True, ip="1.2.3.4")

    async def slow_speed(nbytes, timeout):
        await release.wait()
        return Throughput(ok=True, bytes_per_s=1e5, received=nbytes,
                          asked=nbytes, elapsed_s=1.0)

    svc = NetService(
        Cfg(speed_interval=0.0),
        probes=Probes(reach=reach, trace=trace, throughput=slow_speed),
        clock=clock,
    )

    slow = asyncio.create_task(svc.refresh(force_speed=True))
    await asyncio.sleep(0)            # let it reach the blocked speed probe
    second = asyncio.create_task(svc.refresh())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(slow, second)

    assert not svc.state.reach.ok, (
        "a refresh that started before the outage overwrote the reading that saw it"
    )


@pytest.mark.asyncio
async def test_a_failed_lookup_is_recorded_so_the_row_can_age_itself():
    """C2's data half: keeping the old address is right, but something has to
    mark it as not re-confirmed, or the renderer cannot say so."""
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    spy.traces = [Trace(ok=True, ip="1.2.3.4"), Trace(ok=False, error="no exit ip")]
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    assert svc.state.trace_error == ""
    clock.advance(cfg.trace_interval)
    await svc.refresh()

    assert svc.state.trace.ip == "1.2.3.4"
    assert svc.state.trace_error, "a failed lookup left no trace of itself"


@pytest.mark.asyncio
async def test_cancel_stops_the_loop_without_awaiting():
    """I4. `on_unmount` is not a coroutine, so the teardown it needs cannot be
    `await stop()`."""
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy)
    svc.start()
    assert svc.started
    svc.cancel()
    assert not svc.started
