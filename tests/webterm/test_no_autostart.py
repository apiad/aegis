"""Under systemd, `aegis web` must not start a daemon of its own.

Review Focus 2: `aegis-server.service` owns the daemon. A web process that
also calls `ensure_daemon` races it on boot and can leave a second daemon
holding the root, which is the failure the whole daemon programme exists to
remove.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from aegis.config.roots import AegisRoots
from aegis.daemon.lifecycle import socket_path
from aegis.webterm.server import connect_for


async def test_no_autostart_refuses_instead_of_spawning(tmp_path, monkeypatch):
    spawned = []

    async def _never(*a, **kw):
        spawned.append(1)
        raise AssertionError("ensure_daemon was called with autostart off")

    monkeypatch.setattr("aegis.webterm.server.ensure_daemon", _never)
    connect = connect_for(tmp_path, autostart=False)
    with pytest.raises(ConnectionError) as e:
        await connect()
    assert spawned == []
    assert str(socket_path(AegisRoots.for_project(tmp_path))) in str(e.value), (
        "the refusal must name the socket it looked for"
    )


async def test_no_autostart_still_connects_to_a_daemon_that_is_there(tmp_path):
    sock = socket_path(AegisRoots.for_project(tmp_path))
    sock.parent.mkdir(parents=True, exist_ok=True)
    server = await asyncio.start_unix_server(lambda r, w: w.close(), path=str(sock))
    try:
        reader, writer = await connect_for(tmp_path, autostart=False)()
        writer.close()
        # Awaited, not just closed: an un-awaited transport is finalised after
        # the loop has gone and raises out of __del__, which pytest reports as
        # an unraisable-exception warning on an otherwise green run.
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


async def test_autostart_is_still_the_default(tmp_path, monkeypatch):
    """A laptop `aegis web` with nothing running must still work."""
    called = []

    async def _ensure(root, **kw):
        called.append(root)
        raise RuntimeError("stop here; the call is the assertion")

    monkeypatch.setattr("aegis.webterm.server.ensure_daemon", _ensure)
    with pytest.raises(RuntimeError):
        await connect_for(tmp_path)()
    assert called == [tmp_path]
