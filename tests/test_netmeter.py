"""The NET row's strings. Pure functions, an explicit clock, no network."""
from __future__ import annotations

from aegis.net.probe import Reach, Throughput, Trace
from aegis.net.service import NetState
from aegis.themes import AegisColors
from aegis.tui.fit import strip_markup
from aegis.tui.netmeter import RTT_WARN_MS, format_exit_ip, format_net_tiers

PAL = AegisColors(
    ready="green", working="yellow", error="red", accent="cyan", muted="grey50",
    ok="green", err="red", user="white", user_bg="black",
)
NOW = 1000.0


def _plain(tiers) -> str:
    return " | ".join(strip_markup(t) for t in tiers)


def test_nothing_sampled_yet_shows_an_ellipsis_not_a_zero():
    """A zero claims a measurement. Same rule `_context` follows when it
    falls back to a tier rather than drawing a 0% bar."""
    got = _plain(format_net_tiers(NetState(), PAL, NOW))
    assert "⋯" in got
    assert "0ms" not in got


def test_a_live_link_shows_the_round_trip_time():
    state = NetState(reach=Reach(ok=True, rtt_ms=18.4), reach_at=NOW)
    got = _plain(format_net_tiers(state, PAL, NOW))
    assert "✓ 18ms" in got


def test_a_dead_link_says_no_egress_unmistakably():
    state = NetState(reach=Reach(ok=False, error="unreachable"), reach_at=NOW)
    tiers = format_net_tiers(state, PAL, NOW)
    assert "no egress" in _plain(tiers)
    assert PAL.error in tiers[0], "the failure is not coloured as one"


def test_a_healthy_round_trip_is_not_coloured_as_a_warning():
    state = NetState(reach=Reach(ok=True, rtt_ms=RTT_WARN_MS - 1), reach_at=NOW)
    assert PAL.ready in format_net_tiers(state, PAL, NOW)[0]


def test_a_slow_round_trip_is_coloured_as_a_warning():
    """Threshold bound from the module, not restated as a literal."""
    state = NetState(reach=Reach(ok=True, rtt_ms=RTT_WARN_MS + 1), reach_at=NOW)
    assert PAL.working in format_net_tiers(state, PAL, NOW)[0]


def test_the_widest_tier_carries_the_colo_and_the_speed():
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        trace=Trace(ok=True, ip="1.2.3.4", colo="MIA"), trace_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=139_712, received=1_000_000,
                         asked=1_000_000, elapsed_s=7.16),
        speed_at=NOW - 240.0,
    )
    widest = strip_markup(format_net_tiers(state, PAL, NOW)[0])
    assert "MIA" in widest
    # 139712 B/s * 8 / 1e6 = 1.12 Mbps. Derived, not written.
    assert f"{139_712 * 8 / 1e6:.1f} Mbps" in widest


def test_the_round_trip_time_is_in_every_tier():
    """It is the one reading with no other surface, so it must survive the
    narrowest column."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        trace=Trace(ok=True, ip="1.2.3.4", colo="MIA"), trace_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=1e5, received=10, asked=10,
                         elapsed_s=1.0),
        speed_at=NOW,
    )
    for tier in format_net_tiers(state, PAL, NOW):
        assert "18ms" in strip_markup(tier)


def test_the_colo_is_the_first_thing_to_go():
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        trace=Trace(ok=True, ip="1.2.3.4", colo="MIA"), trace_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=1e5, received=10, asked=10,
                         elapsed_s=1.0),
        speed_at=NOW,
    )
    tiers = [strip_markup(t) for t in format_net_tiers(state, PAL, NOW)]
    assert "MIA" in tiers[0]
    assert "MIA" not in tiers[1]
    assert "Mbps" in tiers[1]


def test_a_throughput_reading_always_carries_its_age():
    """A rate stops being true the moment the network changes, and an undated
    one invites a decision from a reading taken in another building."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=1e5, received=10, asked=10,
                         elapsed_s=1.0),
        speed_at=NOW - 240.0,
    )
    assert "4m ago" in strip_markup(format_net_tiers(state, PAL, NOW)[0])


def test_a_truncated_transfer_is_not_shown_as_a_rate():
    """Review Focus 4, at the surface. 200 KB of a megabyte computes a
    plausible figure, and putting it on screen is how it gets believed."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=9e6, received=200_000,
                         asked=1_000_000, elapsed_s=0.02),
        speed_at=NOW,
    )
    assert "Mbps" not in strip_markup(format_net_tiers(state, PAL, NOW)[0])


def test_the_exit_ip_is_one_row_with_one_tier():
    """One tier deliberately: `fit_rows` drops a segment whose narrowest
    still overflows, and a truncated address reads as a different address."""
    state = NetState(trace=Trace(ok=True, ip="2a0d:5600:6:202::15"), trace_at=NOW)
    tiers = format_exit_ip(state, PAL)
    assert len(tiers) == 1
    assert "2a0d:5600:6:202::15" in strip_markup(tiers[0])


def test_no_exit_ip_means_no_row():
    assert format_exit_ip(NetState(), PAL) == ()


def test_no_service_at_all_paints_nothing():
    """`network.enabled: false` builds no service, so nothing is ever pushed
    and SYSTEM must be exactly what it was before this feature existed. This
    is the case that must NOT show the `⋯` placeholder."""
    assert format_net_tiers(None, PAL, NOW) == ()
    assert format_exit_ip(None, PAL) == ()


def test_a_captive_portal_reads_as_reachable_with_no_address():
    """Review Focus 2, at the surface. The handshake completes, so the row
    honestly says the port answered — and the absence of an IP and a colo is
    the signal that nothing beyond it worked."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=4.0), reach_at=NOW,
        trace=Trace(ok=False, error="no exit ip"), trace_at=NOW,
    )
    widest = strip_markup(format_net_tiers(state, PAL, NOW)[0])
    assert "✓ 4ms" in widest
    assert "MIA" not in widest
    assert format_exit_ip(state, PAL) == ()
