"""One daemon per root, however many clients race to start it.

`ensure_daemon` probed the socket and then spawned with nothing atomic
between, so every client that timed out spawned another daemon, and each
new daemon unlinked the previous one's socket on its way up. On 2026-09-13
that was four daemons in eighty seconds, one of them holding port 8899
with no socket and no registry entry, and `aegis` hanging for 20s.

The lock is an flock the daemon holds for its whole life. The kernel drops
it when the process dies, SIGKILL included, so a crash needs no cleanup the
way a stale socket file does.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle


def test_the_lock_lives_under_this_roots_state_dir(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert lifecycle.lock_path(roots) == roots.state_dir / "daemon.lock"


def test_a_second_lock_on_one_root_is_refused(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    first = lifecycle.acquire_daemon_lock(roots)
    assert first is not None
    try:
        assert lifecycle.acquire_daemon_lock(roots) is None
        assert lifecycle.daemon_lock_held(roots)
    finally:
        first.release()
    again = lifecycle.acquire_daemon_lock(roots)
    assert again is not None, "a released lock stayed taken"
    again.release()
    assert not lifecycle.daemon_lock_held(roots)


def test_a_client_probe_does_not_make_a_booting_daemon_give_up(tmp_path):
    """A client's probe holds the lock for an instant. A daemon that tried
    in that instant and exited would turn a probe into a missing daemon."""
    roots = AegisRoots.for_project(tmp_path)
    probe = lifecycle.acquire_daemon_lock(roots)
    assert probe is not None
    threading.Timer(0.2, probe.release).start()
    lock = lifecycle.acquire_daemon_lock(roots, wait_s=2.0)
    assert lock is not None, "the daemon gave up while a probe held the lock"
    lock.release()


async def test_ensure_daemon_waits_for_a_booting_daemon_instead_of_spawning(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    roots = AegisRoots.for_project(tmp_path)
    booting = lifecycle.acquire_daemon_lock(roots)
    assert booting is not None
    sock = lifecycle.socket_path(roots)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_detached",
                        lambda root: spawned.append(root))

    async def handle(reader, writer):
        writer.close()

    async def come_up():
        await asyncio.sleep(0.3)
        return await asyncio.start_unix_server(handle, path=str(sock))

    task = asyncio.create_task(come_up())
    try:
        got = await lifecycle.ensure_daemon(tmp_path, timeout_s=5)
        assert got == sock
        assert spawned == [], (
            "a client spawned a second daemon beside one that was booting")
    finally:
        (await task).close()
        booting.release()


# --- real `aegis serve` processes --------------------------------------

_CONFIG = """agents:
  main:
    provider: claude-code
    model: sonnet
default_agent: main
"""


def _world(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".aegis.yaml").write_text(_CONFIG)
    # Inherited AEGIS_* would point these daemons at the operator's
    # registry, and the tests may themselves run inside an aegis session.
    env = {k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")}
    env.update(AEGIS_DAEMON_DIR=str(tmp_path / "daemons"),
               AEGIS_IDLE_TIMEOUT="0")
    return root, env, lifecycle.socket_path(AegisRoots.for_project(root))


def _serve(root: Path, env: dict[str, str], log: Path) -> subprocess.Popen:
    with log.open("wb") as fh:
        return subprocess.Popen(
            [sys.executable, "-m", "aegis", "serve", "--cwd", str(root),
             "--autostarted"],
            cwd=root, env=env, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)


def _accepts(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _wait(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _logs(tmp_path: Path) -> str:
    return "\n".join(f"--- {p.name}\n{p.read_text(errors='replace')[-1500:]}"
                     for p in sorted(tmp_path.glob("serve*.log")))


def _stop(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(p.pid, signal.SIGTERM)
    for p in procs:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=5)


def _up(proc: subprocess.Popen, sock: Path, tmp_path: Path) -> None:
    assert _wait(lambda: _accepts(sock) or proc.poll() is not None, 90), (
        _logs(tmp_path))
    assert proc.poll() is None and _accepts(sock), _logs(tmp_path)


@pytest.mark.slow
def test_racing_serves_leave_exactly_one_daemon(tmp_path):
    root, env, sock = _world(tmp_path)
    procs = [_serve(root, env, tmp_path / f"serve{i}.log") for i in range(4)]
    try:
        assert _wait(lambda: sum(p.poll() is None for p in procs) <= 1, 90), (
            f"{sum(p.poll() is None for p in procs)} daemons still running "
            f"for one root\n{_logs(tmp_path)}")
        live = [p for p in procs if p.poll() is None]
        assert len(live) == 1, _logs(tmp_path)
        _up(live[0], sock, tmp_path)
        losers = [i for i, p in enumerate(procs) if p is not live[0]]
        for i in losers:
            assert procs[i].returncode == 1, _logs(tmp_path)
            assert "already running" in (
                tmp_path / f"serve{i}.log").read_text(), _logs(tmp_path)
    finally:
        _stop(procs)


@pytest.mark.slow
def test_a_second_serve_leaves_the_first_ones_socket_alone(tmp_path):
    root, env, sock = _world(tmp_path)
    first = _serve(root, env, tmp_path / "serve0.log")
    procs = [first]
    try:
        _up(first, sock, tmp_path)
        inode = sock.stat().st_ino
        second = _serve(root, env, tmp_path / "serve1.log")
        procs.append(second)
        assert second.wait(timeout=90) == 1, _logs(tmp_path)
        assert sock.stat().st_ino == inode, (
            "the refused daemon replaced the running one's socket")
        assert first.poll() is None and _accepts(sock)
    finally:
        _stop(procs)


@pytest.mark.slow
def test_a_killed_daemon_does_not_block_the_next_one(tmp_path):
    """The kernel releases an flock when its holder dies. A lock that
    outlived a SIGKILL would lock the user out until someone deleted a file
    by hand."""
    root, env, sock = _world(tmp_path)
    first = _serve(root, env, tmp_path / "serve0.log")
    procs = [first]
    try:
        _up(first, sock, tmp_path)
        os.killpg(first.pid, signal.SIGKILL)
        first.wait(timeout=10)
        second = _serve(root, env, tmp_path / "serve1.log")
        procs.append(second)
        _up(second, sock, tmp_path)
    finally:
        _stop(procs)
