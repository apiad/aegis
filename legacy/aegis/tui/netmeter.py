"""The NET rows' strings — pure, with the clock passed in.

Beside `sysmeter.py` for the same reason it exists: a formatter that the
sidebar and the status bar both reach is not the sidebar's business, and a
function taking `(state, colors, now)` is testable without an app.
"""

from __future__ import annotations

from aegis.render_shared import format_age

# Past this, the path is worth a second look even though it works. Chosen to
# sit well above a healthy anycast resolver (tens of ms) and well below a
# timeout, so it flags a degraded link rather than a distant one.
RTT_WARN_MS = 200.0

_LABEL = "NET"
# Aligns the address under the label's own text rather than under the row.
_INDENT = " " * (len(_LABEL) + 2)


def _speed_segment(state, colors, now: float) -> str:
    """`↓ 1.1 Mbps 4m ago`, or nothing.

    Nothing when the transfer did not deliver what it asked for: a stream
    that dies part-way computes a perfectly plausible rate, and the screen is
    where a plausible wrong number gets believed.

    Always with its age. A rate stops being true the moment the network
    changes, and an undated one invites a decision from a reading taken
    somewhere else.
    """
    speed = getattr(state, "speed", None)
    if speed is None or not speed.complete:
        return ""
    mbps = speed.bytes_per_s * 8 / 1e6
    age = format_age(max(0.0, now - state.speed_at))
    return f"↓ {mbps:.1f} Mbps [{colors.muted}]{age}[/]"


def format_net_tiers(state, colors, now: float) -> tuple[str, ...]:
    """The `NET` row, widest tier first.

    `fit_rows` takes the widest that fits, so the ordering here decides what
    a narrow column keeps: the RTT is in every tier because it is the one
    reading with no other surface, the colo goes first because it is
    interesting rather than actionable, and the rate goes second because
    `/net` reprints it on demand.
    """
    head = f"[{colors.muted}]{_LABEL}[/]  "
    # Two different absences. No state at all means `network.enabled: false`:
    # no service was ever built, so SYSTEM must look exactly as it did before
    # this feature. A state that has not sampled yet is a service that exists
    # and has not answered, which is worth a row — and the row says `⋯`
    # rather than `0ms`, because a zero claims a measurement nobody made.
    if state is None:
        return ()
    if not state.sampled:
        return (f"{head}[{colors.muted}]· ⋯[/]",)
    found = state.reach
    if not found.ok:
        return (f"{head}[{colors.error}]✗ no egress[/]",)

    style = colors.working if found.rtt_ms >= RTT_WARN_MS else colors.ready
    live = f"[{style}]✓ {found.rtt_ms:.0f}ms[/]"
    trace = state.trace
    colo = trace.colo if trace is not None and trace.ok else ""
    speed = _speed_segment(state, colors, now)

    tiers = (
        " · ".join(part for part in (live, colo, speed) if part),
        " · ".join(part for part in (live, speed) if part),
        live,
    )
    # Deduplicated: with no colo and no rate all three are the same string,
    # and a tuple that repeats itself misreports how far the row can narrow.
    return tuple(f"{head}{tier}" for tier in dict.fromkeys(tiers))


def format_exit_ip(state, colors, now: float) -> tuple[str, ...]:
    """The exit-IP row. One tier, deliberately.

    `fit_rows` drops a segment whose narrowest tier still overflows rather
    than truncating it, so a column too narrow for the address loses the row
    whole — which is what we want, because `2a0d:5600:6:2…` reads as a
    different address rather than a clipped one. That is the failure `gauge`
    already hit when it truncated a RAM tail to `9.8/1`. A second, shorter
    tier would recreate it.
    """
    trace = getattr(state, "trace", None)
    if trace is None or not trace.ip:
        return ()
    row = f"{_INDENT}[{colors.muted}]{trace.ip}[/]"
    # An address that could not be re-confirmed is dated rather than shown as
    # current. Move from home to a café whose portal completes the 443
    # handshake but blocks the lookup hosts: reach succeeds, both lookups fail,
    # the service rightly keeps the last address — and without this the panel
    # shows your HOME address beside a green tick with nothing marking it old.
    if getattr(state, "trace_error", ""):
        row += f" [{colors.muted}]{format_age(max(0.0, now - state.trace_at))}[/]"
    return (row,)
