"""Ctrl+D detaches. Ctrl+Q quits, and may stop the daemon.

Before this they were one key with one meaning, and the meaning was
detach: `Ctrl+Q` left the brain running and there was no way to close a
daemon from inside the TUI at all.

The dangerous half is stopping the daemon, so the condition is narrow and
structural rather than remembered. A client may stop a daemon only when it
is the last one attached AND that daemon was started by a client's
autostart. The daemon on the VPS is started by systemd as `aegis serve`,
so no client can ever stop it, and that holds without anyone having to
avoid the key.

The discriminator is a property of the DAEMON, not of how this client
connected. "Did I attach by hand" would have answered the same question
today and stopped answering it the moment `aegis` learns to point at a
remote, which stage 5b is about to do. It also gets the local case wrong:
detach once and reattach, and an invocation-scoped rule would call your
own laptop daemon untouchable.
"""
from __future__ import annotations

import pytest
import json

from aegis.daemon import registry as dreg
from aegis.daemon.registry import DaemonRecord


def test_a_client_autostarted_daemon_records_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path))
    rec = DaemonRecord(root=tmp_path, pid=1, socket=tmp_path / "s",
                       started=1.0, version="0", autostarted=True)
    dreg.record(rec)
    assert dreg.daemon_for(tmp_path).autostarted is True


def test_a_daemon_started_by_hand_is_not_autostarted(tmp_path, monkeypatch):
    """`aegis serve` run by a person or by systemd. The default, so that
    the field only ever grants permission explicitly."""
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path))
    dreg.record(DaemonRecord(root=tmp_path, pid=1, socket=tmp_path / "s",
                             started=1.0, version="0"))
    assert dreg.daemon_for(tmp_path).autostarted is False


def test_a_record_written_before_this_field_is_not_autostarted(
        tmp_path, monkeypatch):
    """Reading an older registry must not grant permission it never gave.
    Erring here costs a daemon that outlives its client, which `aegis kill`
    resolves; erring the other way kills someone's VPS."""
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path))
    dreg.record(DaemonRecord(root=tmp_path, pid=1, socket=tmp_path / "s",
                             started=1.0, version="0"))
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text())
    payload.pop("autostarted", None)
    path.write_text(json.dumps(payload))

    assert dreg.daemon_for(tmp_path).autostarted is False


def test_the_autostart_spawn_marks_the_daemon(monkeypatch):
    """The marker has to reach the daemon, and `_spawn_detached` is the one
    place that knows the difference."""
    from aegis.daemon import lifecycle

    seen: dict = {}
    monkeypatch.setattr(lifecycle.subprocess, "Popen",
                        lambda argv, **kw: seen.setdefault("argv", argv))
    lifecycle._spawn_detached(lifecycle.Path("/tmp"))

    assert "--autostarted" in seen["argv"], (
        "a daemon spawned by a client cannot be told apart from one a "
        f"person started: {seen['argv']}")


# --- the keys ------------------------------------------------------------

def _bindings() -> dict:
    from aegis.tui.app import AegisApp
    return {b.key: b.action for b in AegisApp.BINDINGS}


def test_ctrl_d_detaches_and_f4_takes_the_queues_dashboard():
    b = _bindings()
    assert b["ctrl+d"] == "detach", "ctrl+d must detach, as in a shell"
    assert b["f4"] == "open_dashboard", (
        "the queues dashboard moved off ctrl+d and must land somewhere; f4 "
        "sits with f2 config and f3 tasks")
    assert b["ctrl+q"] == "quit"


def test_detach_leaves_the_brain_alone_and_asks_for_no_stop():
    """Detach is the safe key and must stay incapable of stopping a
    daemon, whatever the quit path grows into."""
    import asyncio

    from aegis.tui.app import AegisApp

    app = AegisApp.__new__(AegisApp)
    app._owns_brain = False
    app._voice = None
    app.quit_stops_daemon = False
    exited: list = []
    app.exit = lambda *a, **kw: exited.append(True)

    class _Idx:
        def stop(self): ...
    app._file_indexer = _Idx()

    asyncio.run(app.action_detach())

    assert exited, "detach did not end the view"
    assert app.quit_stops_daemon is False, \
        "detach asked for the daemon to be stopped"


# --- quit may stop the daemon, under two conditions ----------------------

async def _serve_with(tmp_path, monkeypatch, *, autostarted: bool):
    """A real daemon, marked as a client started it or as a person did."""
    import asyncio

    from aegis.cli import _serve
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots
    from aegis.daemon import lifecycle

    from tests.views.conftest import FakeMCP

    class _H:
        async def start(self): ...
        async def send(self, t): ...
        async def close(self): ...

        async def events(self):
            if False:
                yield

    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "0")
    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    roster = {"opus": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
    task = asyncio.create_task(_serve(
        roots=roots, agents=roster, default_agent="opus",
        make_session=lambda a, u, h, **kw: _H(), mcp=FakeMCP(),
        stop=stop, views=True, autostarted=autostarted))
    async with asyncio.timeout(25):
        while not lifecycle.socket_path(roots).exists():
            await asyncio.sleep(0.02)
    return roots, stop, task


async def _quit_over_the_socket(roots, view_id="tty-q"):
    """Attach, press Ctrl+Q, and report whether the daemon went down."""
    import asyncio

    from aegis.daemon import lifecycle
    from aegis.daemon.protocol import FrameDecoder, encode_data, hello

    reader, writer = await asyncio.open_unix_connection(
        str(lifecycle.socket_path(roots)))
    writer.write(hello(view_id, 80, 24))
    await writer.drain()
    decoder = FrameDecoder()
    try:
        async with asyncio.timeout(4):
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                decoder.feed(chunk)
    except TimeoutError:
        pass
    writer.write(encode_data(b"\x11"))       # ctrl+q
    await writer.drain()
    try:
        async with asyncio.timeout(8):
            while await reader.read(65536):
                pass
    except TimeoutError:
        pass
    writer.close()


@pytest.mark.slow
async def test_quit_stops_a_daemon_the_client_autostarted(tmp_path,
                                                          monkeypatch):
    """Asserted on the stop signal rather than on the task finishing.

    Shutdown takes as long as it takes, so waiting on the task measures
    teardown and calls a slow one a refusal. The decision is the event.
    """
    import asyncio

    roots, stop, task = await _serve_with(tmp_path, monkeypatch,
                                          autostarted=True)
    try:
        await _quit_over_the_socket(roots)
        async with asyncio.timeout(10):
            while not stop.is_set():
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)


@pytest.mark.slow
async def test_quit_leaves_a_daemon_a_person_started(tmp_path, monkeypatch):
    """`aegis serve` under systemd on the VPS. No key in any TUI may stop
    it, and this is the assertion that says so."""
    import asyncio

    roots, stop, task = await _serve_with(tmp_path, monkeypatch,
                                          autostarted=False)
    try:
        await _quit_over_the_socket(roots)
        await asyncio.sleep(2)
        assert not stop.is_set(), (
            "a client stopped a daemon it did not start; on the VPS that "
            "is the agent plane going down from someone's keystroke")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)


@pytest.mark.slow
async def test_quit_leaves_a_daemon_another_client_is_still_using(
        tmp_path, monkeypatch):
    import asyncio

    from aegis.daemon import lifecycle
    from aegis.daemon.protocol import hello

    roots, stop, task = await _serve_with(tmp_path, monkeypatch,
                                          autostarted=True)
    other = None
    try:
        _r, other = await asyncio.open_unix_connection(
            str(lifecycle.socket_path(roots)))
        other.write(hello("tty-other", 80, 24))
        await other.drain()
        await asyncio.sleep(2)

        await _quit_over_the_socket(roots, view_id="tty-q")
        await asyncio.sleep(2)
        assert not stop.is_set(), \
            "quitting one view stopped a daemon another view was attached to"
    finally:
        if other is not None:
            other.close()
        stop.set()
        await asyncio.wait_for(task, timeout=30)


# --- a turn in flight is worth one question ------------------------------

def _view_app(*, working: bool, can_stop: bool):
    """A detached view app with a brain holding one session."""
    from aegis.core.session import AgentState
    from aegis.tui.app import AegisApp

    app = AegisApp.__new__(AegisApp)
    app._owns_brain = False
    app._voice = None
    app.quit_stops_daemon = False
    app._can_stop_daemon = lambda: can_stop

    class _Idx:
        def stop(self): ...
    app._file_indexer = _Idx()

    class _Session:
        state = AgentState.working if working else AgentState.ready
        handle = "busy"

    class _Mgr:
        def list_sessions(self):
            return [_Session()]

        _sessions = [_Session()]

    app.manager = _Mgr()
    return app


async def test_quit_asks_before_stopping_a_daemon_mid_turn(monkeypatch):
    """Ctrl+Q is explicit, so it does not ask in general. It asks here
    because the cost is an agent's turn, which no keystroke should spend
    silently."""
    app = _view_app(working=True, can_stop=True)
    asked: list = []

    async def _confirm(_self, _screen):
        asked.append(True)
        return None                      # the user backs out

    monkeypatch.setattr(type(app), "push_screen_wait", _confirm,
                        raising=False)
    exited: list = []
    app.exit = lambda *a, **kw: exited.append(True)

    await app.action_quit()

    assert asked, "stopped a daemon mid-turn without asking"
    assert not exited, "backing out of the question still quit"
    assert app.quit_stops_daemon is False


async def test_quit_does_not_ask_when_nothing_is_running(monkeypatch):
    app = _view_app(working=False, can_stop=True)
    asked: list = []

    async def _confirm(_self, _screen):
        asked.append(True)
        return None

    monkeypatch.setattr(type(app), "push_screen_wait", _confirm,
                        raising=False)
    app.exit = lambda *a, **kw: None

    await app.action_quit()

    assert not asked, "asked about an idle daemon"
    assert app.quit_stops_daemon is True


async def test_quit_does_not_ask_when_it_would_not_stop_anything(
        monkeypatch):
    """Another client is attached, or the daemon is not one a client
    started. Quitting costs this view and nothing else, so there is
    nothing to warn about even mid-turn."""
    app = _view_app(working=True, can_stop=False)
    asked: list = []

    async def _confirm(_self, _screen):
        asked.append(True)
        return None

    monkeypatch.setattr(type(app), "push_screen_wait", _confirm,
                        raising=False)
    app.exit = lambda *a, **kw: None

    await app.action_quit()

    assert not asked, "asked a question whose answer changes nothing"
