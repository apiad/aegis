"""`/net` — a forced reading, printed with the inputs it came from."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.commands import REGISTRY
from aegis.net.probe import Reach, Throughput, Trace
from aegis.net.service import NetState


class FakeService:
    def __init__(self, state):
        self._state = state
        self.forced = []

    @property
    def state(self):
        return self._state

    async def refresh(self, *, force_speed=False):
        self.forced.append(force_speed)


def _ctx(service):
    return SimpleNamespace(
        bridge=SimpleNamespace(net_service=service), handle="h-1"
    )


def _state():
    return NetState(
        reach=Reach(
            ok=True, rtt_ms=18.0, anchor="1.1.1.1:443",
            per_anchor=(("1.1.1.1:443", 18.0), ("8.8.8.8:443", None)),
        ),
        reach_at=0.0,
        trace=Trace(ok=True, ip="2a0d:5600:6:202::15", colo="MIA", loc="US"),
        trace_at=0.0,
        speed=Throughput(ok=True, bytes_per_s=139_712, received=1_000_000,
                         asked=1_000_000, elapsed_s=7.16),
        speed_at=0.0,
    )


def test_the_command_is_registered():
    assert "net" in REGISTRY


@pytest.mark.asyncio
async def test_it_forces_a_throughput_reading():
    """The timer ships off, so asking is the whole point of the command."""
    svc = FakeService(_state())
    await REGISTRY["net"].run(_ctx(svc), {})
    assert svc.forced == [True]


@pytest.mark.asyncio
async def test_it_prints_the_exit_ip_the_colo_and_the_location():
    got = await REGISTRY["net"].run(_ctx(FakeService(_state())), {})
    assert got.ok
    assert "2a0d:5600:6:202::15" in got.body
    assert "MIA" in got.body
    assert "US" in got.body


@pytest.mark.asyncio
async def test_it_prints_a_reading_for_every_anchor_not_just_the_winner():
    got = await REGISTRY["net"].run(_ctx(FakeService(_state())), {})
    assert "1.1.1.1:443" in got.body
    assert "8.8.8.8:443" in got.body


@pytest.mark.asyncio
async def test_it_prints_the_inputs_the_rate_was_computed_from():
    """A number with its inputs is arguable; without them it is oracular."""
    got = await REGISTRY["net"].run(_ctx(FakeService(_state())), {})
    assert f"{139_712 * 8 / 1e6:.1f} Mbps" in got.body
    assert "1000000" in got.body
    assert "7.16" in got.body


@pytest.mark.asyncio
async def test_a_truncated_transfer_is_reported_as_unmeasured():
    """Review Focus 4 at this surface too: the rate is plausible and wrong,
    so the command names the shortfall instead of printing the figure."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0, per_anchor=(("1.1.1.1:443", 18.0),)),
        reach_at=0.0,
        speed=Throughput(ok=True, bytes_per_s=9e6, received=200_000,
                         asked=1_000_000, elapsed_s=0.02),
        speed_at=0.0,
    )
    got = await REGISTRY["net"].run(_ctx(FakeService(state)), {})
    assert "Mbps" not in got.body
    assert "200000" in got.body


@pytest.mark.asyncio
async def test_a_dead_link_says_so_in_the_title():
    state = NetState(
        reach=Reach(ok=False, error="unreachable",
                    per_anchor=(("1.1.1.1:443", None),)),
        reach_at=0.0,
    )
    got = await REGISTRY["net"].run(_ctx(FakeService(state)), {})
    assert "no egress" in got.title


@pytest.mark.asyncio
async def test_it_explains_itself_when_probing_is_switched_off():
    ctx = SimpleNamespace(bridge=SimpleNamespace(net_service=None), handle="h-1")
    got = await REGISTRY["net"].run(ctx, {})
    assert not got.ok
    assert "network" in got.title.lower() or "network" in got.body.lower()
