"""One browser, one daemon connection, and nothing changed in between."""

from __future__ import annotations

import asyncio

import pytest

from aegis.daemon.protocol import encode_data, encode_meta, hello, resize
from aegis.webterm.relay import relay

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
