"""Socket paths, the idle reaper, and autostart."""
import asyncio

import pytest

from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle


class _Registry:
    def __init__(self, views):
        self._views = list(views)

    def list(self):
        return list(self._views)


class _Manager:
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def list_sessions(self):
        return list(self._sessions)


def test_the_socket_lives_under_this_roots_state_dir(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert lifecycle.socket_path(roots) == roots.state_dir / "daemon.sock"


def test_the_default_idle_timeout_is_thirty_minutes(monkeypatch):
    monkeypatch.delenv("AEGIS_IDLE_TIMEOUT", raising=False)
    assert lifecycle.idle_timeout_s() == 1800.0


def test_zero_disables_the_idle_timeout(monkeypatch):
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "0")
    assert lifecycle.idle_timeout_s() == 0.0


def test_a_garbage_idle_timeout_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "soon")
    assert lifecycle.idle_timeout_s() == 1800.0


async def test_an_idle_daemon_is_reaped():
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Manager([]), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    await asyncio.wait_for(reaper.run(), timeout=5)
    assert stop.is_set()


async def test_a_daemon_with_a_view_is_not_reaped():
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry(["term-a"]), _Manager([]), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.3)
    assert not stop.is_set()
    task.cancel()


async def test_a_daemon_with_a_live_session_is_not_reaped():
    """The VPS case. `aegis serve` there always holds sessions, so the
    timer never arms and the daemon never dies."""
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Manager(["agent-1"]), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.3)
    assert not stop.is_set()
    task.cancel()


async def test_the_idle_clock_restarts_when_a_view_attaches():
    """Idleness is a contiguous run, not a total. A daemon used every 20
    minutes for a day must never be reaped mid-use."""
    stop = asyncio.Event()
    registry = _Registry([])
    reaper = lifecycle.IdleReaper(
        registry, _Manager([]), timeout_s=0.3, stop=stop, interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.2)
    registry._views.append("term-a")       # someone attached
    await asyncio.sleep(0.2)
    registry._views.clear()                # and left again
    await asyncio.sleep(0.15)
    assert not stop.is_set(), "the clock did not restart on attach"
    task.cancel()


async def test_a_zero_timeout_never_reaps():
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Manager([]), timeout_s=0.0, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.2)
    assert not stop.is_set()
    task.cancel()


async def test_a_manager_that_cannot_be_asked_is_never_reaped():
    """Never reap on a guess: if we cannot tell whether sessions are live,
    the answer is 'not idle'."""
    class _Broken:
        def list_sessions(self):
            raise RuntimeError("mid-teardown")

    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Broken(), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.3)
    assert not stop.is_set()
    task.cancel()


async def test_ensure_daemon_returns_the_existing_socket_without_spawning(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    roots = AegisRoots.for_project(tmp_path)
    sock = lifecycle.socket_path(roots)
    sock.parent.mkdir(parents=True, exist_ok=True)

    async def handle(reader, writer):
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(sock))
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_detached",
                        lambda root: spawned.append(root))
    try:
        got = await lifecycle.ensure_daemon(tmp_path, timeout_s=5)
        assert got == sock
        assert spawned == [], "autostart ran against a live daemon"
    finally:
        server.close()


async def test_a_stale_socket_file_does_not_count_as_a_live_daemon(
        tmp_path, monkeypatch):
    """A SIGKILLed daemon leaves the file behind. Liveness is 'it accepts a
    connection', never 'the file exists' — trusting the file would hang
    every `aegis` against a dead socket."""
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    roots = AegisRoots.for_project(tmp_path)
    sock = lifecycle.socket_path(roots)
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.write_bytes(b"")
    monkeypatch.setattr(lifecycle, "_spawn_detached", lambda root: None)
    with pytest.raises(lifecycle.SpawnFailed):
        await lifecycle.ensure_daemon(tmp_path, timeout_s=0.5)


async def test_ensure_daemon_raises_when_the_spawn_never_listens(
        tmp_path, monkeypatch):
    """A daemon that dies during boot must fail the attach with a real
    error, not hang the terminal forever."""
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    monkeypatch.setattr(lifecycle, "_spawn_detached", lambda root: None)
    with pytest.raises(lifecycle.SpawnFailed):
        await lifecycle.ensure_daemon(tmp_path, timeout_s=0.5)


def test_python_dash_m_aegis_is_runnable():
    """_spawn_detached launches `sys.executable -m aegis serve`, because the
    daemon must start from a uvx or bare-venv context where the console
    script may not be on PATH. Without __main__.py that fails with
    `No module named aegis.__main__` — on a stderr pointed at /dev/null,
    which is to say silently. Assert the module, not the message."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "aegis", "--version"],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr
    assert "aegis" in (r.stdout + r.stderr).lower()
