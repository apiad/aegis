"""``aegis serve --detach``: checks in the foreground, the server in its own
session, and a return once it listens (#137). Real processes throughout."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

# Each test boots a real server: a few seconds, over the unmarked budget.
pytestmark = pytest.mark.slow


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    r.mkdir()
    (r / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    return r


@pytest.fixture
def reap():
    """Kill whatever detached server a test leaves behind, by its pid file."""
    pid_files: list[Path] = []
    yield pid_files
    for f in pid_files:
        if f.is_file():
            pid = int(f.read_text())
            if psutil.pid_exists(pid):
                os.kill(pid, signal.SIGTERM)
                psutil.Process(pid).wait(timeout=10)


def _detach(root: Path, port: int, flag: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "aegis",
            "serve",
            flag,
            "--root",
            str(root),
            "--port",
            str(port),
        ],
        capture_output=True,
        text=True,
        timeout=40,
        **kw,
    )


@pytest.mark.parametrize("flag", ["--detach", "-d"])
def test_detach_returns_once_listening_and_leaves_the_server(root, reap, flag):
    port = _free_port()
    state = root / ".aegis" / "state"
    reap.append(state / "serve.pid")
    r = _detach(root, port, flag)
    assert r.returncode == 0, r.stderr
    # It returned, and the server is already up: no waiting after the prompt.
    assert _listening(port)
    token = (state / "token").read_text().strip()
    pid = int((state / "serve.pid").read_text())
    assert f"open http://127.0.0.1:{port}/?token={token}" in r.stdout
    assert f"kill {pid}" in r.stdout
    server = psutil.Process(pid)
    assert any(c.laddr.port == port for c in server.net_connections(kind="tcp"))
    # Its own session, so a terminal or SSH hangup does not reach it.
    assert os.getsid(pid) == pid
    assert "aegis serving" in (state / "serve.log").read_text()


def test_a_taken_port_fails_in_the_foreground(root, reap):
    reap.append(root / ".aegis" / "state" / "serve.pid")
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        port = squatter.getsockname()[1]
        r = _detach(root, port, "-d")
    assert r.returncode == 1
    assert f"port {port}" in r.stderr
    assert not (root / ".aegis" / "state" / "serve.pid").exists()


def test_a_server_that_dies_at_boot_is_reported(root, reap, tmp_path):
    # Interpreter start-up in the detached child, and only there, exits: the
    # parent's command line carries -d, the child's does not.
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "import os, sys\n"
        "if 'serve' in sys.argv and '-d' not in sys.argv:\n"
        "    print('boom at boot', flush=True)\n"
        "    os._exit(3)\n"
    )
    state = root / ".aegis" / "state"
    reap.append(state / "serve.pid")
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(hook)}
    start = time.monotonic()
    r = _detach(root, port, "-d", env=env)
    assert r.returncode == 1
    assert "exited with 3" in r.stderr
    assert "boom at boot" in r.stderr, "the end of the log is shown"
    assert time.monotonic() - start < 20, "a dead child is noticed, not waited out"
    assert not (state / "serve.pid").exists()
    assert not _listening(port)


def test_detach_keeps_the_origins_and_opens_the_window(root, reap, tmp_path):
    log = tmp_path / "browser.log"
    exe = tmp_path / "fake-browser"
    exe.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    exe.chmod(0o755)
    state = root / ".aegis" / "state"
    reap.append(state / "serve.pid")
    port = _free_port()
    r = subprocess.run(
        [sys.executable, "-m", "aegis", "serve", "-d", "--window", "--browser", str(exe),
         "--origin", "https://dev.example", "--root", str(root), "--port", str(port)],
        capture_output=True, text=True, timeout=40,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    token = (state / "token").read_text().strip()
    assert f"open https://dev.example/?token={token}" in r.stdout
    # The background server got --origin too: it prints the public URL itself.
    assert (
        f"open https://dev.example/?token={token}" in (state / "serve.log").read_text()
    )
    end = time.monotonic() + 5
    while not log.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert log.read_text().split() == [f"--app=http://127.0.0.1:{port}/?token={token}"]
