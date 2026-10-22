"""A real aegis in multi-user mode (#548): the server runs as the test user
under ``systemd-run`` with CAP_SETUID, CAP_SETGID and CAP_SETPCAP, and two
members, aegis-t-ben and aegis-t-cara, are real Linux users created with sudo.

The harness is tests/fake_claude.py behind a wrapper that first writes what
its process is (user, home, capabilities, environment, pid, cgroup) to a
shared directory, then execs the fake, so its pid stays the harness pid.
Members cannot read the checkout (a home is often 0750), so both live in a
0755 directory under /tmp and run with the system's python3.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from aegis.links import PROTO

MEMBERS = ("aegis-t-ben", "aegis-t-cara")
CAPS = "CAP_SETUID CAP_SETGID CAP_SETPCAP"
FAKE = Path(__file__).parents[1] / "fake_claude.py"
VENV_BIN = Path(sys.executable).parent

WRAPPER = """#!/usr/bin/python3 -I
import json, os, sys
status = open("/proc/self/status").read().splitlines()
report = {{
    "user": os.popen("id -un").read().strip(),
    "home": os.environ.get("HOME"),
    "status": {{l.split(":")[0]: l.split(":", 1)[1].strip() for l in status
               if l.startswith(("Cap", "NoNewPrivs"))}},
    "pool": os.environ.get("AEGIS_TEST_POOL"),
    "server_only": os.environ.get("AEGIS_TEST_SERVER_ONLY"),
    "pid": os.getpid(),
    "cgroup": open("/proc/self/cgroup").read().strip(),
    "argv": sys.argv[1:],
}}
with open(os.path.join({out!r}, f"{{os.getpid()}}.json"), "w") as f:
    json.dump(report, f)
# The fake reads its version from the checkout, which a member cannot reach.
os.environ.setdefault("AEGIS_FAKE_HARNESS_VERSION", {version!r})
os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", {fake!r}, *sys.argv[1:]])
"""


def _tested_max() -> str:
    import tomllib

    data = (
        Path(__file__).parents[2] / "src" / "aegis" / "data" / "tested-harnesses.toml"
    )
    return tomllib.loads(data.read_text())["claude-code"]["max"]


def sudo(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "-n", *args], check=check, capture_output=True, text=True
    )


@pytest.fixture(scope="session")
def host():
    """Skips unless the host can run these tests; creates the two members."""
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        pytest.skip("needs passwordless sudo")
    for tool in ("systemd-run", "setpriv"):
        if shutil.which(tool) is None:
            pytest.skip(f"needs {tool}")
    for name in MEMBERS:
        if subprocess.run(["id", name], capture_output=True).returncode != 0:
            sudo("useradd", "-m", "-s", "/bin/sh", name)
        sudo("chmod", "0750", f"/home/{name}")
    return MEMBERS


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, root: Path, port: int, out: Path, unit: str, tokens: dict):
        self.root, self.port, self.out, self.unit = root, port, out, unit
        self.tokens = tokens
        self.state = root / ".aegis" / "state"
        self._ids = itertools.count(1)

    async def call(self, who: str, op: str, params: dict | None = None):
        """``op`` as ``who`` over /ws, as a browser calls it."""
        url = f"ws://127.0.0.1:{self.port}/ws"
        origin = f"http://127.0.0.1:{self.port}"
        async with connect(url, origin=origin) as ws:
            hello = {"t": "hello", "token": self.tokens[who], "proto": PROTO}
            await ws.send(json.dumps(hello))
            n = next(self._ids)
            await ws.send(
                json.dumps({"t": "call", "id": n, "op": op, "params": params or {}})
            )
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if msg.get("t") == "reply" and msg.get("id") == n:
                    if "error" in msg:
                        raise RuntimeError(f"{op}: {msg['error']}")
                    return msg.get("result")

    async def watch(self, who: str, channel: str, until, timeout: float = 30) -> list:
        """The messages ``channel`` sends ``who`` until ``until(messages)`` is
        true; raises with the last few when it never is."""
        url = f"ws://127.0.0.1:{self.port}/ws"
        origin = f"http://127.0.0.1:{self.port}"
        seen: list = []
        async with connect(url, origin=origin) as ws:
            hello = {"t": "hello", "token": self.tokens[who], "proto": PROTO}
            await ws.send(json.dumps(hello))
            await ws.send(json.dumps({"t": "sub", "channel": channel}))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), end - time.monotonic())
                except TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("channel") == channel:
                    seen.append(msg)
                    if until(seen):
                        return seen
        raise AssertionError(f"{channel} never matched; last: {seen[-3:]}")

    def reports(self) -> list[dict]:
        out = []
        for f in sorted(self.out.glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except ValueError:
                pass  # being written
        return out

    def harness(self, user: str) -> list[dict]:
        """The wrapper's reports for ``user``'s session harnesses."""
        return [
            r
            for r in self.reports()
            if r["user"] == user and "--version" not in r["argv"]
        ]


async def until(check, timeout: float = 20, what: str = "it"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if v := check():
            return v
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def mu_server(host):
    base = Path(tempfile.mkdtemp(prefix="aegis-mu-", dir="/tmp"))
    base.chmod(0o755)
    out = base / "out"
    out.mkdir()
    out.chmod(0o1777)
    bindir = base / "bin"
    bindir.mkdir()
    shutil.copy(FAKE, bindir / "fake_claude.py")
    (bindir / "fake_claude.py").chmod(0o644)
    wrapper = bindir / "claude"
    wrapper.write_text(
        WRAPPER.format(
            out=str(out), fake=str(bindir / "fake_claude.py"), version=_tested_max()
        )
    )
    wrapper.chmod(0o755)
    root = base / "root"
    root.mkdir()
    pool = base / "pool.env"
    pool.write_text("AEGIS_TEST_POOL=from-pool\n")
    pool.chmod(0o600)
    (root / ".aegis.yaml").write_text(
        f"multi_user: {{env_file: {pool}}}\n"
        "default_agent: opus\n"
        "agents:\n"
        "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
        "queues:\n"
        "  general: {agent: opus, max_parallel: 2}\n"
        "terminals:\n"
        "  probe: {cmd: 'sh -c \"sleep 30\"'}\n"
    )
    aegis = str(VENV_BIN / "aegis")
    tokens = {}
    for name in MEMBERS:
        r = subprocess.run(
            [aegis, "token", "add", name, "--root", str(root)],
            capture_output=True,
            text=True,
            check=True,
        )
        tokens[name] = r.stdout.strip()
    subprocess.run(
        [aegis, "token", "list", "--root", str(root)], capture_output=True, check=True
    )
    tokens["admin"] = (root / ".aegis" / "state" / "token").read_text().strip()
    port = _free_port()
    unit = f"aegis-mu-{secrets.token_hex(4)}"
    sudo(
        "systemd-run", "--quiet", "--collect", f"--unit={unit}",
        f"--uid={os.getuid()}", f"--gid={os.getgid()}",
        "-p", f"AmbientCapabilities={CAPS}", "-p", f"CapabilityBoundingSet={CAPS}",
        "-p", "Delegate=yes",
        "-E", "AEGIS_TEST_SERVER_ONLY=1", "-E", f"PATH={os.environ['PATH']}",
        "-E", f"HOME={os.environ['HOME']}",
        f"--working-directory={root}",
        aegis, "serve", "--root", str(root), "--port", str(port),
        "--claude", str(wrapper),
    )  # fmt: skip
    s = Server(root, port, out, unit, tokens)
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end:
            with socket.socket() as c:
                if c.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        else:
            log = sudo("journalctl", "-u", unit, "--no-pager", check=False).stdout
            raise AssertionError(f"the server did not listen:\n{log[-3000:]}")
        yield s
    finally:
        sudo("systemctl", "stop", unit, check=False)
        shutil.rmtree(base, ignore_errors=True)
