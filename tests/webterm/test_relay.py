"""One browser, one daemon connection, and nothing changed in between."""

from __future__ import annotations

import asyncio

import pytest

from aegis.daemon.protocol import (
    FrameDecoder,
    encode_data,
    encode_meta,
    hello,
    parse_hello,
    resize,
)
from aegis.webterm.relay import ATTACHED, RECONNECTING, relay

from tests.webterm.fakes import FakeBrowser, FakeDaemon, until


@pytest.fixture
async def daemon(tmp_path):
    d = FakeDaemon(tmp_path / "d.sock")
    await d.start()
    yield d
    await d.stop()


async def test_the_hello_and_the_input_reach_the_daemon_verbatim(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect))
    first = hello("web-1", 100, 30)
    b.push(first)
    b.push(encode_data(b"ls\r") + resize(90, 20))
    await until(
        lambda: (
            daemon.received
            and bytes(daemon.received[0])
            == first + encode_data(b"ls\r") + resize(90, 20)
        )
    )
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_the_daemons_bytes_reach_the_browser_unchanged(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect))
    b.push(hello("web-1", 100, 30))
    await until(lambda: daemon.received)
    stream = encode_data(bytes(range(256)) * 40) + encode_meta({"type": "exit"})
    for i in range(0, len(stream), 777):  # split mid-frame on purpose
        await daemon.say(0, stream[i : i + 777])
    await until(lambda: b.screen == stream)
    b.leave()
    await asyncio.wait_for(task, 5)


@pytest.mark.parametrize(
    "first",
    [
        "a text message",
        encode_data(b"keys before any hello"),
        b"M\x00\x00\x00\x03{x}",
        hello("../escape", 80, 24),
        hello("web-1", 0, 24),
    ],
)
async def test_a_first_message_that_is_not_a_hello_opens_nothing(first, daemon):
    calls = []

    async def connect():
        calls.append(1)
        return await daemon.connect()

    b = FakeBrowser()
    b.push(first)
    await asyncio.wait_for(relay(b, connect), 5)
    assert calls == [], (
        "the relay reached the daemon for a client that never said hello"
    )


async def test_the_browser_leaving_closes_the_daemon_connection(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect))
    b.push(hello("web-1", 100, 30))
    await until(lambda: daemon.received)
    b.leave()
    await asyncio.wait_for(task, 5)
    await asyncio.wait_for(daemon.eof[0].wait(), 5)


FAST = (0.01, 0.02, 0.05)


def _first_frame(data: bytes):
    kind, payload = next(iter(FrameDecoder().feed(bytes(data))))
    return kind, payload


async def test_a_dropped_daemon_is_reconnected_with_the_same_view_at_the_latest_size(
    daemon,
):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    b.push(resize(70, 20))
    await until(lambda: resize(70, 20) in bytes(daemon.received[0]))
    await daemon.hang_up(0)

    await until(lambda: RECONNECTING in b.texts)
    await until(lambda: len(daemon.received) == 2 and daemon.received[1])
    kind, payload = _first_frame(daemon.received[1])
    assert kind == "M" and parse_hello(payload) == ("web-1", 70, 20, None)

    await daemon.say(1, encode_data(b"fresh view"))
    await until(lambda: ATTACHED in b.texts)
    kinds = [
        k
        for k, p in b.events
        if (k, p) in (("text", ATTACHED), ("bytes", encode_data(b"fresh view")))
    ]
    assert kinds == ["text", "bytes"], "the page must reset before the new view's bytes"
    assert not task.done(), "the browser was dropped"
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_keys_typed_while_the_daemon_was_gone_are_not_replayed(daemon):
    attempts = []
    typed = asyncio.Event()

    async def flaky():
        attempts.append(1)
        if len(attempts) == 2:
            await typed.wait()  # fail only once the key is queued
            raise ConnectionRefusedError
        return await daemon.connect()

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, flaky, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    await daemon.hang_up(0)
    await until(lambda: RECONNECTING in b.texts)
    b.push(encode_data(b"rm -rf typed into the void"))
    await asyncio.sleep(0.05)  # the relay's browser reader queues it
    typed.set()
    await until(lambda: len(daemon.received) == 2 and daemon.received[1])
    await asyncio.sleep(0.1)
    assert b"rm -rf" not in bytes(daemon.received[1])
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_keys_typed_while_the_daemon_is_being_started_are_not_replayed(daemon):
    """The real reconnect spends its gap inside connect(): ensure_daemon
    spawning a daemon can take seconds, and the connect then succeeds.
    Dropping stale input only before connect() let these keys through
    (seen in review)."""
    attempts = []
    typed = asyncio.Event()

    async def spawning():
        attempts.append(1)
        if len(attempts) == 2:
            await typed.wait()  # still spawning when the key is typed
        return await daemon.connect()

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, spawning, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    await daemon.hang_up(0)
    await until(lambda: RECONNECTING in b.texts)
    b.push(encode_data(b"rm -rf typed into the void"))
    await asyncio.sleep(0.05)
    typed.set()
    await until(lambda: len(daemon.received) == 2 and daemon.received[1])
    await asyncio.sleep(0.1)
    assert b"rm -rf" not in bytes(daemon.received[1])
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_a_refusal_is_not_announced_as_attached(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    await daemon.hang_up(0)
    await until(lambda: len(daemon.received) >= 2)
    await daemon.hang_up(1)  # the daemon refuses: no bytes, just EOF
    await until(lambda: len(daemon.received) >= 3)
    assert ATTACHED not in b.texts
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_a_browser_leaving_during_the_backoff_ends_the_relay():
    async def never():
        raise ConnectionRefusedError

    b = FakeBrowser()
    b.push(hello("web-1", 100, 30))
    task = asyncio.create_task(relay(b, never, delays=(0.05,)))
    await asyncio.sleep(0.2)
    b.leave()
    await asyncio.wait_for(task, 2)
