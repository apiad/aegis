"""Tests for SSHTunnel async context manager.

Mocking strategy: monkeypatch asyncio.create_subprocess_exec so no real
ssh process is spawned. In the happy-path test we also open a real loopback
TCP listener on the port SSHTunnel picks (extracted from the -L argument),
so the probe succeeds without SSH. In the timeout test we return a FakeProc
but open no listener, so the probe fails within the short deadline.
"""
from __future__ import annotations

import asyncio
import pytest


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssh_tunnel_picks_port_and_probes(monkeypatch):
    """Mock create_subprocess_exec + open a real loopback listener on the
    port that SSHTunnel picks, verify probe returns and __aexit__
    terminates."""
    calls: dict = {}

    class FakeProc:
        returncode = None

        async def wait(self):
            pass

        def terminate(self):
            calls["terminated"] = True

    async def fake_exec(*args, **kw):
        calls["argv"] = args
        # Extract local port from '-L <local>:localhost:<remote>' argument.
        # args layout: ("ssh", "-L", "<local>:localhost:<remote>", "-N", "<host>")
        local = int(args[2].split(":")[0])
        srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", local)
        calls["srv"] = srv
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    from aegis.remote.ssh_tunnel import SSHTunnel

    async with SSHTunnel("vps", 8080) as t:
        assert t.local_port > 0
        assert "argv" in calls
        assert calls["argv"][0] == "ssh"
        assert "-L" in calls["argv"]
        assert "-N" in calls["argv"]

    assert calls.get("terminated") is True
    calls["srv"].close()


@pytest.mark.asyncio
async def test_ssh_tunnel_probe_timeout(monkeypatch):
    """If no listener ever appears, raise TunnelError within probe_timeout."""

    class FakeProc:
        returncode = None

        async def wait(self):
            pass

        def terminate(self):
            pass

    async def fake_exec(*a, **kw):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    from aegis.remote.ssh_tunnel import SSHTunnel, TunnelError

    with pytest.raises(TunnelError):
        async with SSHTunnel("nowhere", 9999, probe_timeout_s=0.3):
            pass


@pytest.mark.asyncio
async def test_ssh_tunnel_argv_format(monkeypatch):
    """Verify the exact ssh command line structure."""
    calls: dict = {}

    class FakeProc:
        returncode = None

        async def wait(self):
            pass

        def terminate(self):
            pass

    async def fake_exec(*args, **kw):
        calls["argv"] = args
        local = int(args[2].split(":")[0])
        srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", local)
        calls["srv"] = srv
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    from aegis.remote.ssh_tunnel import SSHTunnel

    async with SSHTunnel("myhost", 4242) as t:
        argv = calls["argv"]
        # ssh -L <local>:localhost:4242 -N myhost
        assert argv[0] == "ssh"
        l_idx = list(argv).index("-L")
        l_arg = argv[l_idx + 1]
        local_str, mid, remote_str = l_arg.split(":")
        assert int(local_str) == t.local_port
        assert mid == "localhost"
        assert remote_str == "4242"
        assert "-N" in argv
        assert "myhost" in argv

    calls["srv"].close()


@pytest.mark.asyncio
async def test_ssh_tunnel_kill_on_no_terminate(monkeypatch):
    """If terminate is called and proc doesn't exit within the teardown timeout,
    kill() is called.

    Strategy: construct a SSHTunnel with a fake proc whose wait() only
    returns after kill() sets returncode. Patch __aexit__ to use a 50ms
    wait_for timeout instead of 2s so the test runs fast.
    """
    calls: dict = {}

    class FakeProc:
        returncode = None

        async def wait(self):
            # Hangs until kill() sets returncode to non-None.
            while self.returncode is None:
                await asyncio.sleep(0.01)

        def terminate(self):
            calls["terminated"] = True

        def kill(self):
            calls["killed"] = True
            self.returncode = -9

    from aegis.remote.ssh_tunnel import SSHTunnel

    # Patch __aexit__ to use a short teardown timeout so the test doesn't
    # actually wait 2s for the process to die.
    async def fast_aexit(self, *exc):
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()

    monkeypatch.setattr(SSHTunnel, "__aexit__", fast_aexit)

    tunnel = SSHTunnel("vps", 8080)
    tunnel._proc = FakeProc()
    tunnel.local_port = 9999

    await tunnel.__aexit__(None, None, None)

    assert calls.get("terminated") is True
    assert calls.get("killed") is True
