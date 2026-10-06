"""The aegis process log, and whether a crash actually reaches disk.

The failure this exists to stop is not "the traceback was wrong" — it is
"there was no traceback to read". So the tests that matter here assert on
the *file*, after the thing that crashed has finished crashing.
"""
from __future__ import annotations

import logging

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.state import aegis_log
from aegis.tui.app import AegisApp


@pytest.fixture(autouse=True)
def _reset_log_module():
    """The module holds process-global state (handler, hooks). Restore it so
    one test's configuration cannot leak into the next."""
    import sys
    import threading
    prev = (aegis_log._log_path, aegis_log._handler, aegis_log._context,
            aegis_log._hooks_installed, sys.excepthook, threading.excepthook)
    yield
    if aegis_log._handler is not None:
        logging.getLogger("aegis").removeHandler(aegis_log._handler)
        aegis_log._handler.close()
    (aegis_log._log_path, aegis_log._handler, aegis_log._context,
     aegis_log._hooks_installed, sys.excepthook,
     threading.excepthook) = prev


def _read(tmp_path) -> str:
    return (tmp_path / "aegis.log").read_text(encoding="utf-8")


# --- the file itself ------------------------------------------------------

def test_configure_creates_the_log_and_records_the_open(tmp_path):
    target = aegis_log.configure(tmp_path, install_hooks=False)
    assert target == tmp_path / "aegis.log"
    assert target.exists()
    assert "aegis log opened" in _read(tmp_path)


def test_write_is_a_noop_before_configure(tmp_path):
    """Callers must never have to guard, so an unconfigured write is silent
    rather than an exception on a path that is already going wrong."""
    aegis_log.write("nobody is listening")
    aegis_log.crash("nowhere", ValueError("x"))
    assert not (tmp_path / "aegis.log").exists()


def test_crash_writes_banner_traceback_and_is_flushed(tmp_path):
    aegis_log.configure(tmp_path, install_hooks=False)
    try:
        raise ValueError("the specific thing that broke")
    except ValueError as e:
        aegis_log.crash("tui", e)

    # Read without closing the handler: the point is that it is on disk
    # already, not that it lands when the process exits.
    text = _read(tmp_path)
    assert aegis_log.CRASH_MARK in text
    assert "[tui] ValueError: the specific thing that broke" in text
    assert "test_crash_writes_banner_traceback_and_is_flushed" in text


class _Wrapper(Exception):
    """Shaped like textual.worker.WorkerFailed: the real failure lives in
    `.error`, and the wrapper's own traceback names nothing useful."""

    def __init__(self, error):
        self.error = error
        super().__init__(f"Worker raised exception: {error!r}")


def test_crash_unwraps_an_exception_carried_as_a_payload(tmp_path):
    """Every pane mount runs in a Textual worker, so the DuplicateIds crash
    arrives wrapped. Logging only the wrapper records that something failed
    somewhere — which is not debuggable."""
    aegis_log.configure(tmp_path, install_hooks=False)
    try:
        raise ValueError("the real failure")
    except ValueError as inner:
        aegis_log.crash("tui", _Wrapper(inner))

    text = _read(tmp_path)
    assert "carried by _Wrapper" in text
    assert "ValueError: the real failure" in text
    # The frames are the whole point of unwrapping.
    assert "test_crash_unwraps_an_exception_carried_as_a_payload" in text


def test_crash_ignores_a_payload_that_was_never_raised(tmp_path):
    """An exception-valued arg with no traceback is data, not a cause;
    printing it would be noise dressed as evidence."""
    aegis_log.configure(tmp_path, install_hooks=False)
    aegis_log.crash("tui", _Wrapper(ValueError("never raised")))
    assert "carried by" not in _read(tmp_path)


def test_crash_carries_the_context_provider(tmp_path):
    aegis_log.configure(tmp_path, context=lambda: "3 tabs; live: apt-abel",
                        install_hooks=False)
    aegis_log.crash("tui", RuntimeError("boom"))
    assert "context: 3 tabs; live: apt-abel" in _read(tmp_path)


def test_a_raising_context_provider_never_eats_the_crash(tmp_path):
    def _bad():
        raise KeyError("provider is broken too")

    aegis_log.configure(tmp_path, context=_bad, install_hooks=False)
    aegis_log.crash("tui", RuntimeError("boom"))
    text = _read(tmp_path)
    assert "RuntimeError: boom" in text
    assert "context unavailable: KeyError" in text


def test_stdlib_aegis_loggers_land_in_the_file(tmp_path):
    """`aegis.scheduler` and friends already call logger.exception; nothing
    was configured to catch them, so those records went nowhere."""
    aegis_log.configure(tmp_path, install_hooks=False)
    logging.getLogger("aegis.scheduler").error("scheduler fire failed: nightly")
    assert "scheduler fire failed: nightly" in _read(tmp_path)


# --- reading it back ------------------------------------------------------

def test_tail_crashes_only_keeps_the_indented_block(tmp_path):
    aegis_log.configure(tmp_path, install_hooks=False)
    aegis_log.write("ordinary line one")
    aegis_log.crash("asyncio", ValueError("kaboom"))
    aegis_log.write("ordinary line two")

    kept = aegis_log.tail(200, crashes_only=True)
    body = "\n".join(kept)
    assert "kaboom" in body
    assert "ordinary line one" not in body
    assert "ordinary line two" not in body
    # The traceback under the banner survives the filter, or the filter is
    # just a list of headlines.
    assert any(ln.startswith("  ") for ln in kept)


def test_tail_of_a_missing_log_is_empty_not_an_error(tmp_path):
    assert aegis_log.tail(10, log_path=tmp_path / "nope.log") == []


# --- the doors ------------------------------------------------------------

@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_hook_records_an_uncaught_worker_exception(tmp_path):
    import threading
    aegis_log.configure(tmp_path)

    def _boom():
        raise RuntimeError("worker thread died")

    t = threading.Thread(target=_boom, name="doomed")
    t.start()
    t.join()

    text = _read(tmp_path)
    assert "[thread:doomed] RuntimeError: worker thread died" in text


@pytest.mark.asyncio
async def test_asyncio_hook_records_a_never_retrieved_task_exception(
        tmp_path):
    import asyncio
    aegis_log.configure(tmp_path, install_hooks=False)
    loop = asyncio.get_running_loop()
    aegis_log.install_asyncio_hook(loop)

    loop.call_exception_handler({
        "message": "Task exception was never retrieved",
        "exception": ValueError("orphaned task"),
    })
    assert "[asyncio] ValueError: orphaned task" in _read(tmp_path)


# --- end to end: a real TUI crash ----------------------------------------

def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = None

    def __init__(self):
        self.sent = []

    async def start(self): ...
    async def send(self, text): self.sent.append(text)
    async def close(self): ...

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): ...
    async def stop(self): ...


def _factory(agent, mcp_url, handle, **kw):
    return FakeSession()


@pytest.mark.asyncio
async def test_a_tui_crash_reaches_disk_with_the_pane_roster(
        tmp_path, monkeypatch):
    """The whole point, end to end: raise inside the running app and find
    the traceback in `.aegis/state/aegis.log` afterwards — with the roster
    that says which handles were up when it happened."""
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    seen: list[str] = []
    # The app really does die — Textual re-raises on teardown, as it exits
    # non-zero in production. The record has to be on disk regardless.
    with pytest.raises(RuntimeError, match="mount blew up"):
        async with app.run_test() as pilot:
            await pilot.pause()
            seen.append(app._panes[0].handle)
            app._handle_exception(RuntimeError("mount blew up"))
            await pilot.pause()

    handle = seen[0]
    log = (tmp_path / ".aegis" / "state" / "aegis.log").read_text()
    assert "[tui] RuntimeError: mount blew up" in log
    assert handle in log
    assert f"pane-{handle}" in log


@pytest.mark.asyncio
async def test_the_crash_context_names_retired_handles(tmp_path, monkeypatch):
    """A duplicate-handle crash is unreadable without knowing which names
    are retired-but-still-holding-a-DOM-id."""
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    seen: list[str] = []
    with pytest.raises(RuntimeError, match="boom"):
        async with app.run_test() as pilot:
            await pilot.pause()
            seen.append(app._panes[0].handle)
            await app.rename_handle(seen[0], "lucid-river")
            app._handle_exception(RuntimeError("boom"))
            await pilot.pause()

    birth = seen[0]
    log = (tmp_path / ".aegis" / "state" / "aegis.log").read_text()
    assert f"retired handles: {birth}" in log
    assert "lucid-river" in log
