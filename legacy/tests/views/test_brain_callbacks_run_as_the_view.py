"""A brain callback into a widget runs as that widget's own app.

The brain calls observers from its own tasks: an MCP tool handler, the queue,
a harness's stdout reader. Those tasks carry no Textual ``active_app``, or
another view's. A Textual timer copies the context it is started in, so a
timer a widget started from such a callback died with ``LookupError`` on its
first tick, and removing the widget later awaited the dead timer and re-raised
it. The whole view exited: on F10's card pick, and on Ctrl+W.

Built the way the daemon builds views, a ``ViewRegistry`` over one brain, with
no socket.
"""

from __future__ import annotations

import asyncio
import contextvars

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.events import ToolUse
from aegis.tui.fleet_screen import FleetScreen
from aegis.tui.pane import ConversationPane
from aegis.views.registry import ViewRegistry
from textual._context import active_app

from tests.brain import make_brain
from tests.views.conftest import FakeMCP


class _Harness:
    session_id = None

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _registry(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {
        "default": Agent(
            harness="claude-code", model="opus", effort="high", permission="auto"
        )
    }
    mgr = make_brain(
        roster,
        "default",
        make_session=lambda *a, **k: _Harness(),
        mcp=None,
        roots=roots,
    )
    reg = ViewRegistry(
        manager=mgr,
        roots=roots,
        mcp=FakeMCP(),
        agents=roster,
        default_agent="default",
        make_session=lambda *a, **k: _Harness(),
    )
    return reg, mgr


async def _until(cond, rounds=400):
    for _ in range(rounds):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


def _conversations(app) -> list:
    return [p for p in app._panes if isinstance(p, ConversationPane)]


async def _from_a_brain_task(fn) -> None:
    """Run ``fn`` in a task with no app in its context, as the brain's own
    tasks are."""

    async def call():
        fn()

    await contextvars.Context().run(asyncio.create_task, call())


def _key(app, key: bytes) -> None:
    """A keystroke as the daemon delivers it: a data frame into the view."""
    app._driver.feed(b"D" + len(key).to_bytes(4, "big") + key)


def _tool_call() -> ToolUse:
    return ToolUse(name="Bash", summary="make test", raw_input={}, tool_call_id="t1")


async def _two_tabs(reg, mgr):
    view = await reg.open("tty-main", (120, 40))
    await view.run()
    await mgr.spawn("default")
    await mgr.spawn("default")
    app = view.app
    await _until(lambda: len(_conversations(app)) >= 2)
    app.call_next(app._activate, 0)
    await _until(lambda: app._active is app._panes[0])
    return view


async def test_ctrl_w_after_a_tool_call_from_a_brain_task_keeps_the_view(tmp_path):
    reg, mgr = _registry(tmp_path)
    view = await _two_tabs(reg, mgr)
    try:
        app = view.app
        pane = app._active
        ticks = []
        tick = pane._tick_tools
        pane._tick_tools = lambda: (ticks.append(1), tick())

        await _from_a_brain_task(lambda: pane._core._fire_event(_tool_call()))
        assert pane._tool_timer is not None, "the tool call must start the spinner"
        timer = pane._tool_timer
        # Ticked, or died trying: the dead timer is what Ctrl+W tripped over.
        await _until(lambda: ticks or timer._task is None or timer._task.done())

        _key(app, b"\x17")  # ctrl+w
        # Its message loop stops its timers last, and that is where it died.
        await _until(lambda: pane._task is None or view._task.done())
        assert not view._task.done(), "closing the tab took the view down"
        assert ticks, "the spinner's timer died on its first tick"
    finally:
        await reg.close_all()


async def test_f10s_redraw_timer_belongs_to_its_own_view(tmp_path):
    """Two views over one brain: an event reaching F10 in one from a task
    that carries the other's app must not arm a timer bound to the other."""
    reg, mgr = _registry(tmp_path)
    view = await _two_tabs(reg, mgr)
    other = await reg.open("tty-other", (120, 40))
    await other.run()
    try:
        app = view.app
        await reg.open("tty-main", (120, 40), open="fleet")
        await _until(lambda: isinstance(app.screen, FleetScreen))
        screen = app.screen
        await _until(lambda: hasattr(screen, "_ticker"))
        await _until(lambda: len(screen._view().cards) >= 2)
        screen._ticker.pause()
        # A redraw just now, so the event waits for the coalescing timer.
        screen.refresh_fleet()

        core = app._panes[1]._core

        async def in_the_other_view():
            active_app.set(other.app)
            core._fire_event(_tool_call())

        await asyncio.create_task(in_the_other_view())
        assert screen._pending is not None, "the event must arm the coalescing timer"
        assert screen._pending._task.get_context().get(active_app) is app
    finally:
        await reg.close_all()
