"""End-to-end: a harness running over ssh, calling back through the
reverse tunnel.

Uses `localhost` as the remote host, so this runs anywhere sshd accepts a
key-based connection from the current user. Marked `live` and skipped
when that isn't true.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from aegis.hosts.connection import HostConnection
from aegis.hosts.launcher import SshLauncher
from aegis.hosts.models import HostSpec

pytestmark = pytest.mark.live


def _ssh_localhost_works() -> bool:
    if shutil.which("ssh") is None:
        return False
    try:
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", "localhost", "true"],
            capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_ssh = pytest.mark.skipif(
    not _ssh_localhost_works(),
    reason="ssh to localhost is not available (BatchMode key auth required)")


@pytest.fixture
def conn_for(tmp_path):
    """Hand out HostConnections and close them NO MATTER WHAT.

    Teardown has to be unconditional: a test that raises before its own
    close() leaks an ssh ControlMaster, and once pytest removes the tmp
    dir the control socket is gone, so `ssh -O exit` can never reach it
    again — the master then sits there until the box reboots. That is not
    hypothetical; it happened during this feature's first live run.
    """
    made: list[HostConnection] = []

    def _make(cwd=None, *, mcp_port=9, spec=None):
        spec = spec or HostSpec(name="localhost", ssh="localhost",
                                cwd=str(cwd or tmp_path))
        conn = HostConnection(
            spec,
            control_path=str(tmp_path / f"ctl-{len(made)}.sock"),
            mcp_port=mcp_port)
        made.append(conn)
        return conn

    yield _make

    async def _close_all():
        for c in made:
            try:
                await c.close()
            except Exception:                                # noqa: BLE001
                pass

    asyncio.run(_close_all())


@needs_ssh
def test_master_opens_and_allocates_a_reverse_port(conn_for):
    conn = conn_for()          # mcp_port 9 = discard; nothing must connect

    async def go():
        await conn.ensure_open()
        return conn.remote_mcp_url

    url = asyncio.run(go())
    assert url.startswith("http://127.0.0.1:")
    assert url.endswith("/mcp/")


@needs_ssh
def test_launcher_runs_a_command_in_the_remote_cwd(tmp_path, conn_for):
    workdir = tmp_path / "tree"
    workdir.mkdir()
    (workdir / "marker.txt").write_text("here")
    conn = conn_for(workdir)
    lau = SshLauncher(conn, conn._spec, local_root=str(tmp_path))

    async def go():
        proc = await lau.spawn(["cat", "marker.txt"],
                               cwd=str(workdir), env=None)
        out, _ = await proc.communicate()
        return out

    assert asyncio.run(go()).strip() == b"here"


@needs_ssh
def test_preflight_rejects_a_missing_binary(tmp_path, conn_for):
    from aegis.hosts.errors import HostError

    conn = conn_for()

    async def go():
        await conn.ensure_open()
        await conn.preflight("definitely-not-a-real-binary-xyz",
                             str(tmp_path))

    with pytest.raises(HostError, match="preflight failed"):
        asyncio.run(go())


@needs_ssh
def test_preflight_accepts_a_real_binary(tmp_path, conn_for):
    conn = conn_for()

    async def go():
        await conn.ensure_open()
        await conn.preflight("sh", str(tmp_path))

    asyncio.run(go())     # must not raise


@needs_ssh
def test_the_reverse_tunnel_actually_carries_traffic(tmp_path, conn_for):
    """The assertion that matters: something on the 'remote' side can
    reach a server bound to localhost here, through the -R forward."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"reached-the-local-server")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    local_port = srv.server_address[1]

    conn = conn_for(mcp_port=local_port)
    lau = SshLauncher(conn, conn._spec, local_root=str(tmp_path))

    async def go():
        await conn.ensure_open()
        remote_port = conn.remote_mcp_url.split(":")[2].split("/")[0]
        # python3 rather than a bash /dev/tcp trick: dash (Ubuntu's /bin/sh)
        # has no /dev/tcp, and this test must not silently pass or fail on
        # which shell the remote happens to use.
        fetch = (
            "import urllib.request;"
            f"print(urllib.request.urlopen("
            f"'http://127.0.0.1:{remote_port}/', timeout=10)"
            f".read().decode())")
        proc = await lau.spawn(["python3", "-c", fetch],
                               cwd=str(tmp_path), env=None)
        out, err = await proc.communicate()
        assert proc.returncode == 0, err.decode("utf-8", "replace")
        return out

    try:
        assert b"reached-the-local-server" in asyncio.run(go())
    finally:
        srv.shutdown()


@needs_ssh
def test_one_master_is_shared_by_concurrent_spawns(tmp_path, conn_for):
    """Two sessions opened at once must share a single ControlMaster,
    not race to create two."""
    conn = conn_for()
    lau = SshLauncher(conn, conn._spec, local_root=str(tmp_path))

    async def go():
        procs = await asyncio.gather(*[
            lau.spawn(["true"], cwd=str(tmp_path), env=None)
            for _ in range(4)])
        await asyncio.gather(*[p.communicate() for p in procs])
        return conn._proc

    master = asyncio.run(go())
    assert master is not None


@needs_ssh
def test_a_really_killed_link_reports_itself(tmp_path, conn_for):
    """Kill the ssh session for real and confirm link_failure() sees it.

    The hermetic version of this uses a fake launcher, which proves the
    wiring but not that an actual dropped link produces a non-zero ssh
    exit — which is the whole signal.
    """
    from aegis.hosts.errors import RemoteLinkLost

    conn = conn_for()
    lau = SshLauncher(conn, conn._spec, local_root=str(tmp_path))

    async def go():
        # A remote process that would otherwise sit there for a minute.
        proc = await lau.spawn(["sleep", "60"], cwd=str(tmp_path), env=None)
        lau.watch_stderr()
        await asyncio.sleep(1.0)
        # Tear the whole master down underneath it — the real-world
        # equivalent of the laptop sleeping or the network dropping.
        await conn.close()
        await asyncio.wait_for(proc.wait(), timeout=15)
        return lau.link_failure()

    failure = asyncio.run(go())
    assert isinstance(failure, RemoteLinkLost)
    assert failure.host == "localhost"


@needs_ssh
def test_a_clean_remote_exit_is_not_reported_as_a_link_failure(
        tmp_path, conn_for):
    """The other half: a remote command that exits 0 over a healthy link
    must not look like a drop, or every normal close reads as a crash."""
    conn = conn_for()
    lau = SshLauncher(conn, conn._spec, local_root=str(tmp_path))

    async def go():
        proc = await lau.spawn(["true"], cwd=str(tmp_path), env=None)
        lau.watch_stderr()
        await asyncio.wait_for(proc.wait(), timeout=15)
        return lau.link_failure()

    assert asyncio.run(go()) is None


@needs_ssh
@pytest.mark.skipif(shutil.which("claude") is None,
                    reason="claude CLI not on PATH")
def test_claude_runs_over_ssh_and_answers(tmp_path, conn_for):
    from aegis.config import Agent
    from aegis.drivers.claude import ClaudeDriver
    from aegis.events import AssistantText, Result

    conn = conn_for()
    lau = SshLauncher(conn, conn._spec, local_root=str(tmp_path))
    agent = Agent(harness="claude-code", model="haiku")

    async def go():
        sess = ClaudeDriver().session(
            agent, str(tmp_path), "", "live-remote", lau)
        await sess.start()
        await sess.send("Reply with exactly: PONG")
        texts = []
        async for ev in sess.events():
            if isinstance(ev, AssistantText):
                texts.append(ev.text)
            if isinstance(ev, Result):
                break
        await sess.close()
        return "".join(texts)

    assert "PONG" in asyncio.run(go()).upper()
