"""A pane that is gone must be gone from its core's observer lists.

`ConversationPane.__init__` subscribes six callbacks to the core session —
event, state, inbox, dispatch, loop, recap — and `on_unmount` released none
of them: `core/session.py` grew `remove_event_observer` and no sibling, so
there was nothing to call.

That is invisible while a pane and its core die together. It stops being
invisible the moment a core OUTLIVES a pane, which is the whole premise of
the daemon: `_mount_brain_pane` wraps a session the brain owns, views
attach and detach, and `app.py` even drops a half-mounted pane on the floor
when a view detaches mid-mount. Every one of those leaves a detached widget
wired to a live brain. It then does layout work for every event forever —
N stale panes means N times the work per streamed token — and eventually
one of them reaches for a widget that unmounted with it:

    NoMatches: No nodes match '#transcript' on ConversationPane(...)
      pane.py _on_core_state -> _start_indicator -> _transcript()

which `_emit_state` let straight through into Textual's message loop,
because it is the one emitter with no try/except (`_emit_close`,
`_emit_dispatch` and `_emit_inbox` all guard). So the crash landed on the
next message the user sent.

Two independent defects, two tests: the pane must unsubscribe, and the
emitter must not let an observer take the app down.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import Agent
from aegis.core.session import AgentSession, AgentState
from aegis.events import AssistantText, Result


def _agent():
    return Agent(harness="claude-code", model="claude-sonnet-4-5",
                 effort="medium", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent: list[str] = []
        self.session_id = "sid-1"

    async def start(self): pass
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok", usage=None)
        yield Result(duration_ms=1, is_error=False)
    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


def _pane_observers(core) -> list:
    """Every observer on ``core`` that is a bound method of a pane.

    Asserting the lists are EMPTY would be wrong and would rot: a core
    carries observers of its own (the session-log writer, for one). What
    must be true is narrower — no callback still points at a widget.
    """
    from aegis.tui.pane import ConversationPane
    return [cb for lst in (core._extra_state_observers,
                           core._extra_event_observers,
                           core._extra_inbox_observers,
                           core._extra_dispatch_observers,
                           core._loop_observers,
                           core._recap_observers)
            for cb in lst
            if isinstance(getattr(cb, "__self__", None), ConversationPane)]


def _core() -> AgentSession:
    return AgentSession(FakeSession(), _agent(), "sonnet", "h1",
                        project_root=Path.cwd())


def test_emit_state_does_not_let_an_observer_take_the_app_down():
    """Parity with every other emitter. A raising observer is logged and
    the remaining observers still run."""
    core = _core()
    seen: list[str] = []

    def boom(_c, _s, _f):
        raise RuntimeError("detached widget")

    core.add_state_observer(boom)
    core.add_state_observer(lambda _c, _s, _f: seen.append("after"))

    core._emit_state(AgentState.working, finished=False)

    assert seen == ["after"], "a raising observer stopped the ones behind it"


def test_emit_event_does_not_let_an_observer_take_the_app_down():
    core = _core()
    seen: list[str] = []

    def boom(_c, _e):
        raise RuntimeError("detached widget")

    core.add_event_observer(boom)
    core.add_event_observer(lambda _c, _e: seen.append("after"))

    core._fire_event(AssistantText("x", usage=None))

    assert seen == ["after"]


@pytest.mark.asyncio
async def test_an_unmounted_pane_is_unsubscribed_from_a_core_it_outlives(
        tmp_path, monkeypatch):
    """The root cause. Mount a pane over a core the app does NOT own (the
    brain case), unmount it, then drive the core: the dead pane must not be
    called at all."""
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import AegisApp
    from aegis.tui.pane import ConversationPane

    app = AegisApp({"sonnet": _agent()}, "sonnet",
                   lambda *a, **kw: FakeSession(), FakeMCP())
    core = _core()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = ConversationPane(None, _agent(), "sonnet", "brainy-bear",
                                app._palette, core=core,
                                project_root=tmp_path)
        await app.query_one("ContentSwitcher").mount(pane)
        await pilot.pause()

        before = _pane_observers(core)
        assert len(before) >= 4, f"pane never subscribed: {before}"

        await pane.remove()
        await pilot.pause()

        after = _pane_observers(core)
        assert after == [], (
            f"unmounted pane still subscribed: {len(before)} -> {after}")

        # And the proof that matters: the core can still run a turn without
        # the dead pane raising out of it.
        core._emit_state(AgentState.working, finished=False)
        core._fire_event(AssistantText("y", usage=None))


@pytest.mark.asyncio
async def test_a_pane_that_fails_to_mount_is_unsubscribed_too(
        tmp_path, monkeypatch):
    """Textual sends no unmount for a widget that never finished mounting,
    so `on_unmount` cannot be the only release. `_mount_brain_pane` already
    catches a mid-mount detach and drops the pane from `_panes`; it must
    drop it from the brain as well."""
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import AegisApp

    app = AegisApp({"sonnet": _agent()}, "sonnet",
                   lambda *a, **kw: FakeSession(), FakeMCP())
    core = _core()
    core.handle = "brainy-bear"
    core.agent = _agent()
    core.agent_slug = "sonnet"

    async with app.run_test() as pilot:
        await pilot.pause()
        cs = app.query_one("ContentSwitcher")

        async def boom(_pane):
            raise RuntimeError("view detached mid-mount")

        monkeypatch.setattr(type(cs), "mount", boom, raising=False)
        await app._mount_brain_pane(core)
        await pilot.pause()

    assert _pane_observers(core) == [], \
        "half-mounted pane stayed wired to the brain"
