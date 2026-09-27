"""Three network readings, each a value rather than an exception.

A probe that raises makes every caller write the same `try`, and one of them
forgets; worse, in a 1-second UI tick already wrapped in
`contextlib.suppress`, a raise is indistinguishable from "no reading" and
the screen says nothing. So a network failure is a field.

Nothing here imports from `aegis`. That is deliberate: `aegis.config` calls
`parse_anchor` to validate at load time, and a dependency the other way
would be a cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import time
from dataclasses import dataclass

# Two operators, so one having a bad day does not read as "no egress".
DEFAULT_ANCHORS: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("8.8.8.8", 443))

# One ~300-byte response carries the exit IP, the Cloudflare colo and the
# country. `ifconfig.me` carries the address alone, so it is the fallback
# rather than the primary.
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
TRACE_FALLBACK_URL = "https://ifconfig.me/ip"
SPEED_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"

# Below this, a transfer has not been measured: dividing by it yields a
# number with no upper bound, which renders as a triumphant meaningless
# figure rather than as the absence of a reading.
MIN_ELAPSED_S = 0.01


@dataclass(frozen=True)
class Reach:
    """Whether packets leave, and how long the handshake took."""

    ok: bool
    rtt_ms: float = 0.0
    anchor: str = ""  # which anchor supplied `rtt_ms`
    error: str = ""
    # Every anchor's reading, `None` where it did not connect. `/net` prints
    # all of them; the sidebar row has room for the best one.
    per_anchor: tuple[tuple[str, float | None], ...] = ()


@dataclass(frozen=True)
class Trace:
    ok: bool
    ip: str = ""
    colo: str = ""
    loc: str = ""
    error: str = ""


@dataclass(frozen=True)
class Throughput:
    ok: bool
    bytes_per_s: float = 0.0
    received: int = 0
    asked: int = 0
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def complete(self) -> bool:
        """Whether the transfer delivered what it was asked for.

        A stream that dies at 200 KB of a megabyte computes a perfectly
        plausible rate, and only the counts reveal it — so the renderer asks
        this rather than trusting `ok`.
        """
        return self.ok and self.asked > 0 and self.received >= self.asked


def parse_anchor(text: str) -> tuple[str, int]:
    """`"1.1.1.1:443"` or `"[2606:4700::1111]:443"` -> `(host, port)`.

    Partitioned from the RIGHT, and brackets stripped. A left-to-right split
    of `"2606:4700:4700::1111:443"` yields six fields and a host of `"2606"`,
    which connects somewhere else or nowhere and reports it as a healthy
    anchor — a probe lying in the one direction that matters.
    """
    host, sep, port = text.rpartition(":")
    if not sep or not host:
        raise ValueError(f"anchor must be host:port, got {text!r}")
    try:
        number = int(port)
    except ValueError:
        raise ValueError(f"anchor port must be a number, got {port!r}") from None
    if not 0 < number < 65536:
        raise ValueError(f"anchor port out of range: {number}")
    return host.strip("[]"), number


def parse_trace(text: str) -> dict[str, str]:
    """`cdn-cgi/trace`'s `key=value` lines as a dict.

    A line counts only when its key is short and alphanumeric. A captive
    portal answers 200 with an HTML login page, and a permissive parser reads
    `<meta http-equiv="refresh" content="0;url=...">` as a field — which then
    arrives on screen where an exit IP belongs.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key.isalnum() or len(key) > 12:
            continue
        out[key] = value.strip()
    return out


def _is_address(text: str) -> bool:
    """Whether `text` parses as an IPv4 or IPv6 address.

    Parsing *something* out of a response is not the same as parsing an IP.
    This is the guard that keeps `sign-in-required` off the exit-IP row.
    """
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


async def _connect(host: str, port: int, timeout: float) -> float | None:
    """Handshake RTT in milliseconds, or `None` if it did not connect."""
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    rtt = (time.monotonic() - started) * 1000.0
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return rtt


async def reach(
    anchors: tuple[tuple[str, int], ...] = DEFAULT_ANCHORS, timeout: float = 3.0
) -> Reach:
    """Egress liveness and handshake RTT.

    TCP rather than ICMP: `ping` needs `CAP_NET_RAW` or a subprocess per
    sample, and a captive portal — the failure this exists to catch — answers
    ICMP and refuses the connection.

    Every anchor is probed concurrently rather than in sequence. Two
    handshakes in parallel cost the same wall-clock as one, and it is what
    lets `/net` print a reading per anchor instead of only the winner's.
    """
    if not anchors:
        return Reach(ok=False, error="no anchors configured")
    labels = [f"{host}:{port}" for host, port in anchors]
    rtts = await asyncio.gather(
        *(_connect(host, port, timeout) for host, port in anchors)
    )
    per_anchor = tuple(zip(labels, rtts, strict=True))
    live = [(label, rtt) for label, rtt in per_anchor if rtt is not None]
    if not live:
        return Reach(ok=False, error="unreachable", per_anchor=per_anchor)
    label, best = min(live, key=lambda pair: pair[1])
    return Reach(ok=True, rtt_ms=best, anchor=label, per_anchor=per_anchor)


async def trace(
    url: str = TRACE_URL,
    fallback_url: str = TRACE_FALLBACK_URL,
    timeout: float = 5.0,
) -> Trace:
    """The exit IP, plus the colo and country when the primary answers."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            fields = parse_trace(response.text)
            found = fields.get("ip", "")
            if _is_address(found):
                return Trace(
                    ok=True,
                    ip=found,
                    colo=fields.get("colo", ""),
                    loc=fields.get("loc", ""),
                )
        except Exception:  # noqa: BLE001 — httpx raises a family, plus parse errors
            pass
        try:
            response = await client.get(fallback_url)
            response.raise_for_status()
            found = response.text.strip()
            if _is_address(found):
                return Trace(ok=True, ip=found)
        except Exception:  # noqa: BLE001
            pass
    return Trace(ok=False, error="no exit ip")


async def throughput(
    nbytes: int = 1_000_000, url: str = SPEED_URL, timeout: float = 30.0
) -> Throughput:
    """A timed download, reported with the inputs it was computed from."""
    import httpx

    if nbytes <= 0:
        return Throughput(ok=False, asked=nbytes, error="byte count must be positive")
    target = url.format(bytes=nbytes)
    received = 0
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", target) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
    except Exception as exc:  # noqa: BLE001
        return Throughput(
            ok=False,
            received=received,
            asked=nbytes,
            elapsed_s=time.monotonic() - started,
            error=type(exc).__name__,
        )
    elapsed = time.monotonic() - started
    if elapsed < MIN_ELAPSED_S:
        return Throughput(
            ok=False,
            received=received,
            asked=nbytes,
            elapsed_s=elapsed,
            error="too fast to measure",
        )
    return Throughput(
        ok=True,
        bytes_per_s=received / elapsed,
        received=received,
        asked=nbytes,
        elapsed_s=elapsed,
    )
