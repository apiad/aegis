"""``/net`` — a forced network reading, printed with its inputs.

    /net    exit IP, colo, RTT per anchor, and a throughput sample

Takes the throughput reading regardless of ``network.speed_interval``,
because the timer ships off and asking is the point. Read-only.
"""

from __future__ import annotations

from aegis.commands import CommandContext, CommandResult, SlashCommand, register

_SERVICE = None


def _service(ctx):
    """The app's NetService, else a private one on the defaults.

    Mirrors ``builtins/usage.py:_quota_services``: the command has to work
    headlessly under ``aegis serve``, where no TUI app owns a service.

    The fallback uses ``NetworkConfig()`` rather than reading the file. The
    app is the thing that holds the loaded config, so if there is no app there
    is no cheap way to ask, and the defaults are what this command needs
    anyway: it forces its own reading, so the intervals are irrelevant and
    only the anchors and the timeout matter.

    Returns None only when the app exists and has probing switched off —
    ``AegisApp.net_service`` is None in exactly that case, and the difference
    between "off" and "no app" is whether the attribute is present at all.
    """
    global _SERVICE
    bridge = getattr(ctx, "bridge", None)
    if bridge is not None and hasattr(bridge, "net_service"):
        return bridge.net_service  # may be None: probing is configured off
    if _SERVICE is None:
        from aegis.config import NetworkConfig
        from aegis.net.service import NetService

        _SERVICE = NetService(NetworkConfig())
    return _SERVICE


def _lines(state) -> list[str]:
    found = state.reach
    out = [f"egress    {'up' if found.ok else 'DOWN'}"]
    for label, rtt in found.per_anchor:
        out.append(f"  {label:<26}{'—' if rtt is None else f'{rtt:.0f}ms'}")
    trace = state.trace
    if trace is not None and trace.ok:
        out.append(f"exit ip   {trace.ip}")
        if trace.colo:
            where = f"{trace.colo} ({trace.loc})" if trace.loc else trace.colo
            out.append(f"colo      {where}")
    speed = state.speed
    if speed is None:
        return out
    # The inputs, always. A rate with the bytes and the seconds it came from
    # is a number someone can argue with; without them it is an oracle.
    if speed.complete:
        mbps = speed.bytes_per_s * 8 / 1e6
        out.append(
            f"down      {mbps:.1f} Mbps "
            f"({speed.received} bytes in {speed.elapsed_s:.2f}s)"
        )
    else:
        out.append(
            f"down      unmeasured — {speed.error or 'short transfer'} "
            f"({speed.received} of {speed.asked} bytes "
            f"in {speed.elapsed_s:.2f}s)"
        )
    return out


async def _net(ctx: CommandContext, args) -> CommandResult:
    service = _service(ctx)
    if service is None:
        return CommandResult(
            False,
            "network probing is off",
            "set `network.enabled: true` in .aegis.yaml — see docs/configuration.md",
        )
    await service.refresh(force_speed=True)
    state = service.state
    if not state.sampled:
        return CommandResult(False, "net · no reading", "the probe returned nothing")
    return CommandResult(
        True,
        "net · " + ("up" if state.reach.ok else "no egress"),
        "\n".join(_lines(state)),
    )


register(
    SlashCommand(
        "net",
        "exit IP, egress liveness and a throughput reading",
        "/net",
        _net,
    )
)
