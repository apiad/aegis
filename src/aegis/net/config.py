"""The ``network:`` block of ``.aegis.yaml``: what the probes reach, and how often.

Three cadences because the readings differ by a factor of three thousand in
cost. A wrong block keeps every default in force and carries the reason for
aegis doctor, as ``context:`` does, so a typo never stops the server.

Every cadence and budget must be positive. ``interval: 0`` turned the legacy
poller into a hot loop, measured at 16,778 handshakes in 0.25 s, and the
mistake is invited because ``speed_interval: 0`` is documented as "off". That
one may be zero; nothing else may.
"""

from __future__ import annotations

from dataclasses import dataclass

from .probe import DEFAULT_ANCHORS, parse_anchor


@dataclass(frozen=True)
class NetConfig:
    enabled: bool = True
    interval: float = 20.0
    trace_interval: float = 300.0
    speed_interval: float = 900.0  # 0 = off
    speed_bytes: int = 1_000_000
    timeout: float = 3.0
    # The megabyte needs its own budget: a 1 MB body that has not arrived in
    # the handshake's 3 s is an ordinary slow link, the one the probe exists
    # to measure.
    speed_timeout: float = 30.0
    anchors: tuple[tuple[str, int], ...] = DEFAULT_ANCHORS


POSITIVE = ("interval", "trace_interval", "timeout", "speed_timeout")
KEYS = ("enabled", *POSITIVE, "speed_interval", "speed_bytes", "anchors")


def network_from(raw: object) -> tuple[NetConfig, str | None]:
    """The block over its defaults, and why it was refused when it was."""
    default = NetConfig()
    if raw is None:
        return default, None
    if not isinstance(raw, dict):
        return default, "network must be a mapping"
    out: dict = {}
    for k, v in raw.items():
        if k not in KEYS:
            return default, f"network has no key {k!r}; it takes {', '.join(KEYS)}"
        if k == "enabled":
            if not isinstance(v, bool):
                return default, f"network.enabled {v!r} is not true or false"
            out[k] = v
        elif k in POSITIVE:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                return default, f"network.{k} {v!r} is not a positive number of seconds"
            out[k] = float(v)
        elif k == "speed_interval":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                return default, (
                    f"network.speed_interval {v!r} is not 0 (off) or a positive "
                    "number of seconds"
                )
            out[k] = float(v)
        elif k == "speed_bytes":
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                return (
                    default,
                    f"network.speed_bytes {v!r} is not a positive byte count",
                )
            out[k] = v
        else:  # anchors
            if not isinstance(v, list) or not v:
                return default, "network.anchors must be a non-empty list of host:port"
            try:
                out[k] = tuple(parse_anchor(str(a)) for a in v)
            except ValueError as e:
                return default, f"network.anchors: {e}"
    return NetConfig(**out), None
