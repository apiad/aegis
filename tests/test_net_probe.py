"""The three network probes.

Nothing here touches the real network: `reach` runs against a local
`asyncio.start_server`, and the two HTTP probes run on `pytest_httpx`. A
probe test that passes because the internet happened to work is precisely
the failure this feature exists to report.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.net.probe import (
    DEFAULT_ANCHORS,
    MIN_ELAPSED_S,
    SPEED_URL,
    TRACE_FALLBACK_URL,
    TRACE_URL,
    parse_anchor,
    parse_trace,
    reach,
    throughput,
    trace,
)


# ----- parse_anchor -----

def test_parse_anchor_reads_host_and_port():
    assert parse_anchor("1.1.1.1:443") == ("1.1.1.1", 443)


def test_parse_anchor_refuses_an_unbracketed_ipv6_anchor_as_ambiguous():
    """Review Focus 1, corrected after review.

    An earlier version of this test asserted that
    `"2606:4700:4700::1111:443"` parses as host + port. It cannot safely:
    that string is ALSO a valid IPv6 address, so either reading is wrong half
    the time, and guessing "port" mangles a bare address into a host that
    never connects. Brackets are the only unambiguous form, and the error says
    so.
    """
    import ipaddress

    ambiguous = "2606:4700:4700::1111:443"
    ipaddress.ip_address(ambiguous)  # it really is a valid address
    with pytest.raises(ValueError, match="bracket"):
        parse_anchor(ambiguous)


def test_parse_anchor_strips_the_brackets_an_ipv6_literal_may_carry():
    assert parse_anchor("[2606:4700:4700::1111]:443") == (
        "2606:4700:4700::1111", 443,
    )


def test_parse_anchor_refuses_a_bare_host():
    with pytest.raises(ValueError):
        parse_anchor("1.1.1.1")


def test_parse_anchor_refuses_a_non_numeric_port():
    with pytest.raises(ValueError):
        parse_anchor("1.1.1.1:https")


def test_the_default_anchors_are_two_different_operators():
    """One provider having a bad day must not read as 'no egress'."""
    hosts = {h for h, _ in DEFAULT_ANCHORS}
    assert len(hosts) >= 2


# ----- reach -----

@pytest.mark.asyncio
async def test_reach_reports_a_listening_port_with_an_rtt():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        got = await reach((("127.0.0.1", port),), timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()
    assert got.ok
    assert got.rtt_ms >= 0.0
    assert got.anchor == f"127.0.0.1:{port}"
    assert got.per_anchor == ((f"127.0.0.1:{port}", got.rtt_ms),)


@pytest.mark.asyncio
async def test_reach_reports_a_closed_port_as_no_egress():
    # Bind, read the port, close it: nothing else can be listening there.
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    got = await reach((("127.0.0.1", port),), timeout=2.0)
    assert not got.ok
    assert got.error
    assert got.per_anchor == ((f"127.0.0.1:{port}", None),)


@pytest.mark.asyncio
async def test_reach_probes_every_anchor_so_net_can_print_each():
    """One dead and one live anchor: ok overall, and both readings kept."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    live = server.sockets[0].getsockname()[1]
    dead_server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    dead = dead_server.sockets[0].getsockname()[1]
    dead_server.close()
    await dead_server.wait_closed()
    try:
        got = await reach((("127.0.0.1", dead), ("127.0.0.1", live)), timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()

    assert got.ok
    assert got.anchor == f"127.0.0.1:{live}"
    assert dict(got.per_anchor)[f"127.0.0.1:{dead}"] is None
    assert dict(got.per_anchor)[f"127.0.0.1:{live}"] is not None


@pytest.mark.asyncio
async def test_reach_with_no_anchors_is_a_failure_not_a_success():
    """Review Focus 5's probe-side half: an empty list must not read as ok."""
    got = await reach((), timeout=2.0)
    assert not got.ok
    assert got.error


# ----- parse_trace -----

def test_parse_trace_reads_the_fields_cloudflare_returns():
    body = "fl=368f137\nh=www.cloudflare.com\nip=1.2.3.4\ncolo=MIA\nloc=US\n"
    got = parse_trace(body)
    assert got["ip"] == "1.2.3.4"
    assert got["colo"] == "MIA"
    assert got["loc"] == "US"


def test_parse_trace_ignores_a_captive_portals_html():
    """Review Focus 2. A portal answers 200 with a login page; a permissive
    key=value parser turns its meta-refresh into a field, and the sidebar
    then prints markup where an exit IP belongs."""
    body = (
        '<!DOCTYPE html>\n<html><head>\n'
        '<meta http-equiv="refresh" content="0;url=http://portal.example/login">\n'
        "</head><body>Sign in</body></html>\n"
    )
    assert parse_trace(body) == {}


# ----- trace -----

@pytest.mark.asyncio
async def test_trace_returns_the_exit_ip_colo_and_location(httpx_mock):
    httpx_mock.add_response(
        url=TRACE_URL, text="ip=2a0d:5600:6:202::15\ncolo=MIA\nloc=US\n"
    )
    got = await trace()
    assert got.ok
    assert got.ip == "2a0d:5600:6:202::15"
    assert got.colo == "MIA"
    assert got.loc == "US"


@pytest.mark.asyncio
async def test_trace_falls_back_when_the_trace_host_does_not_answer(httpx_mock):
    httpx_mock.add_exception(Exception("boom"), url=TRACE_URL)
    httpx_mock.add_response(url=TRACE_FALLBACK_URL, text="203.0.113.7\n")
    got = await trace()
    assert got.ok
    assert got.ip == "203.0.113.7"
    # The fallback carries an address and nothing else; the renderer simply
    # has no colo to draw.
    assert got.colo == ""


@pytest.mark.asyncio
async def test_trace_refuses_a_value_that_is_not_an_address(httpx_mock):
    """Review Focus 2, the second half: parsing something is not the same as
    parsing an IP. Whatever comes back has to be an address or it is not one."""
    httpx_mock.add_response(url=TRACE_URL, text="ip=sign-in-required\n")
    httpx_mock.add_response(url=TRACE_FALLBACK_URL, text="<html>portal</html>")
    got = await trace()
    assert not got.ok
    assert got.ip == ""


# ----- throughput -----

@pytest.mark.asyncio
async def test_throughput_measures_a_complete_transfer(httpx_mock):
    asked = 4096
    httpx_mock.add_response(url=SPEED_URL.format(bytes=asked), content=b"x" * asked)
    got = await throughput(nbytes=asked)
    assert got.ok
    assert got.complete
    assert got.received == asked
    assert got.asked == asked
    assert got.bytes_per_s > 0


@pytest.mark.asyncio
async def test_throughput_reports_a_truncated_transfer_as_incomplete(httpx_mock):
    """Review Focus 4. 200 KB of 1 MB computes a plausible rate; only the
    counts reveal it, so `complete` is what the renderer asks."""
    asked, short = 1_000_000, 200_000
    httpx_mock.add_response(url=SPEED_URL.format(bytes=asked), content=b"x" * short)
    got = await throughput(nbytes=asked)
    assert got.received == short
    assert got.asked == asked
    assert not got.complete


@pytest.mark.asyncio
async def test_throughput_refuses_a_transfer_too_fast_to_measure(
    httpx_mock, monkeypatch
):
    """Review Focus 3. `received / 0.0` raises and `received / 1e-9` renders a
    triumphant meaningless figure, so a sample under the floor is not a sample.

    A clock that does not advance rather than a small body and a hope: at
    4 KB over loopback the real elapsed time is genuinely near the floor, so
    a timing-based version of this test would pass or fail with the load on
    the machine. Patched on the module's own `time` reference, not on the
    stdlib module, so nothing else in the process is affected.
    """
    from types import SimpleNamespace

    import aegis.net.probe as probe

    asked = 4096
    httpx_mock.add_response(
        url=probe.SPEED_URL.format(bytes=asked), content=b"x" * asked
    )
    monkeypatch.setattr(probe, "time", SimpleNamespace(monotonic=lambda: 100.0))

    got = await probe.throughput(nbytes=asked)
    assert not got.ok
    # Floor read off the module, never restated as a literal here.
    assert got.elapsed_s <= MIN_ELAPSED_S
    assert "too fast" in got.error
    assert got.bytes_per_s == 0.0
    # The counts survive a refusal — `/net` prints them to explain itself.
    assert got.received == asked


@pytest.mark.asyncio
async def test_throughput_refuses_a_non_positive_byte_count():
    """Review Focus 5's probe-side half for speed_bytes."""
    got = await throughput(nbytes=0)
    assert not got.ok
    assert got.error


@pytest.mark.asyncio
async def test_throughput_reports_a_dead_host_without_raising(httpx_mock):
    asked = 4096
    httpx_mock.add_exception(Exception("no route"), url=SPEED_URL.format(bytes=asked))
    got = await throughput(nbytes=asked)
    assert not got.ok
    assert got.error


def test_parse_anchor_refuses_a_port_less_ipv6_literal():
    """I7. `rpartition` on a bare literal takes the last hextet for a port:
    "2606:4700:4700::1111" became ("2606:4700:4700:", 1111), a host that never
    connects — so a working network reads as a permanent `✗ no egress` with
    nothing pointing at the config. A bare IPv4 is already refused, which is
    what makes accepting the IPv6 form surprising.
    """
    for text in ("2606:4700:4700::1111", "::1", "fe80::1"):
        with pytest.raises(ValueError):
            parse_anchor(text)


def test_parse_anchor_takes_the_bracketed_v6_form():
    assert parse_anchor("[2606:4700:4700::1111]:443") == (
        "2606:4700:4700::1111", 443,
    )
    assert parse_anchor("[::1]:8080") == ("::1", 8080)


def test_parse_anchor_refuses_a_bracketed_host_with_no_port():
    with pytest.raises(ValueError):
        parse_anchor("[::1]")


@pytest.mark.asyncio
async def test_throughput_reports_a_complete_transfer_however_fast_it_was():
    """M13, re-graded. The floor exists to stop a division by a hair, but it was
    discarding transfers that DELIVERED — 1 MB under 10ms is above 800 Mbps,
    which a datacentre link does, so the row vanished exactly where the link was
    good. A complete transfer is a reading.
    """
    from types import SimpleNamespace

    import aegis.net.probe as probe

    asked = 1_000_000
    ticks = iter([100.0, 100.0 + 0.002])  # a 2 ms transfer
    monkeypatched = SimpleNamespace(monotonic=lambda: next(ticks))

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(probe, "time", monkeypatched)
        mp.setattr("httpx.AsyncClient", _instant_client(asked))
        got = await probe.throughput(nbytes=asked)

    assert got.ok, f"a complete transfer was discarded: {got.error}"
    assert got.complete
    assert got.bytes_per_s > 0


@pytest.mark.asyncio
async def test_throughput_gives_up_on_a_link_that_only_dribbles():
    """I5. httpx's read timeout caps the GAP between chunks, not the transfer,
    so a link that keeps trickling is never cut off — and because `_loop` awaits
    refresh, the whole NET row freezes for as long as it trickles.
    """
    import aegis.net.probe as probe

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("httpx.AsyncClient", _dribbling_client())
        # Outer bound so the missing budget FAILS rather than hanging the suite:
        # the whole point of the defect is that nothing stops the transfer.
        try:
            got = await asyncio.wait_for(
                probe.throughput(nbytes=1_000_000, timeout=0.05), 5.0
            )
        except (asyncio.TimeoutError, TimeoutError):
            pytest.fail(
                "throughput never gave up: a 0.05s timeout ran past 5s, because "
                "httpx's read timeout caps the gap between chunks, not the transfer"
            )

    assert not got.ok
    assert "timeout" in got.error.lower()
    assert got.received > 0, "the bytes that did arrive were not counted"


def _instant_client(total: int):
    class _Resp:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield b"x" * total

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            return _Stream()

    return _Client


def _dribbling_client():
    class _Resp:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            while True:
                await asyncio.sleep(0.005)
                yield b"x" * 8

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            return _Stream()

    return _Client
