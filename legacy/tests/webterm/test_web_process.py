"""A browser reaches a view through a real `aegis web` and a real daemon.

Real processes, real ports, a real WebSocket: the slice a user touches.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest
from websockets.asyncio.client import connect as ws_connect

from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon.protocol import FrameDecoder, hello

READY = b"type a message"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _daemon_pids(root) -> list[int]:
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = p.info["cmdline"] or []
        if any(c in ("serve", "server") for c in cmd) and str(root) in cmd:
            out.append(p.pid)
    return out


@pytest.mark.slow
async def test_a_browser_reaches_a_view_through_aegis_web(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    port, token = _free_port(), "gate-token"
    (root / ".aegis.yaml").write_text(
        "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
        f"    model: sonnet\nweb:\n  token: {token}\n  port: {port}\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")}
    env.update(AEGIS_DAEMON_DIR=str(tmp_path / "daemons"), AEGIS_IDLE_TIMEOUT="0")
    log = (tmp_path / "web.log").open("wb")
    web = subprocess.Popen(
        [sys.executable, "-m", "aegis", "web", "--no-browser", "--cwd", str(root)],
        cwd=root,
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90
        async with httpx.AsyncClient() as http:
            while True:
                with contextlib.suppress(httpx.HTTPError):
                    if (await http.get(f"{base}/healthz")).status_code == 200:
                        break
                assert web.poll() is None, (tmp_path / "web.log").read_text()
                assert time.monotonic() < deadline, (tmp_path / "web.log").read_text()
                await asyncio.sleep(0.2)
            login = await http.get(f"{base}/?t={token}", follow_redirects=False)
            assert login.status_code == 303

        screen = bytearray()
        decoder = FrameDecoder()
        async with ws_connect(
            f"ws://127.0.0.1:{port}/term",
            additional_headers={"Cookie": f"aegis_web={token}"},
        ) as ws:
            await ws.send(hello("web-gate", 100, 30))
            deadline = time.monotonic() + 60
            while READY not in screen:
                assert time.monotonic() < deadline, bytes(screen[-2000:])
                for kind, payload in decoder.feed(await ws.recv()):
                    if kind == "D":
                        screen.extend(payload)
        assert b"\x1b[?1049h" in screen, "the view never entered the alt screen"

        sock = lifecycle.socket_path(AegisRoots.for_project(root))
        assert sock.exists(), "aegis web did not start a daemon"
        daemons = _daemon_pids(root)
        assert len(daemons) == 1, daemons
        listening = {
            c.laddr.port
            for c in psutil.Process(daemons[0]).net_connections("inet")
            if c.status == psutil.CONN_LISTEN
        }
        assert port not in listening, "the daemon bound the web port"
        assert port in {
            c.laddr.port
            for c in psutil.Process(web.pid).net_connections("inet")
            if c.status == psutil.CONN_LISTEN
        }
    finally:
        for pid in [web.pid, *_daemon_pids(root)]:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            web.wait(timeout=15)
        log.close()
