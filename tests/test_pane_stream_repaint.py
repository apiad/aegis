"""Streamed deltas repaint the widget at a bounded frame rate.

A repaint is a `Static.update()`, which is `refresh(layout=True)`, which
makes Textual rebuild the whole compositor map — cost linear in mounted
widgets. Deltas that arrive back-to-back coalesce on their own, but a
real stream arrives with gaps, so each delta was buying its own reflow:
measured ~28 ms apiece at a full window.

The record stays current on every delta regardless — same contract as a
hidden tab. Only the widget waits.
"""
import pytest
from rich.text import Text

from aegis.config import Agent
from aegis.events import AssistantText
from aegis.tui import pane as pane_mod
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
    async def start(self): pass
    async def send(self, text): self.sent.append(text)
    async def events(self):
        if False:
            yield  # pragma: no cover
    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda *a, **kw: FakeSession(), FakeMCP())


def _painted(pane):
    """What the streaming widget is actually showing."""
    return pane._streaming_block.text_payload()


def _recorded(pane):
    """What the transcript record holds — always the truth."""
    return pane._history[pane._streaming_history_idx].payload


@pytest.mark.asyncio
async def test_deltas_inside_the_frame_window_do_not_repaint(monkeypatch):
    """With the window wide open, only the first delta paints."""
    monkeypatch.setattr(pane_mod, "STREAM_REPAINT_S", 3600.0)
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(4):
            pane._on_core_event(pane._core, AssistantText(f"tok{i} "))
        await pilot.pause()

        assert _recorded(pane) == "tok0 tok1 tok2 tok3 "
        assert _painted(pane) == "tok0 "
        assert pane._repaint_pending is True


@pytest.mark.asyncio
async def test_a_skipped_repaint_is_reconciled(monkeypatch):
    """The deferred flush catches the widget up with the record."""
    monkeypatch.setattr(pane_mod, "STREAM_REPAINT_S", 3600.0)
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(4):
            pane._on_core_event(pane._core, AssistantText(f"tok{i} "))
        await pilot.pause()

        pane._catch_up_streaming_block()
        await pilot.pause()

        assert _painted(pane) == _recorded(pane) == "tok0 tok1 tok2 tok3 "
        assert pane._repaint_pending is False


@pytest.mark.asyncio
async def test_no_throttle_paints_every_delta(monkeypatch):
    """With the window at zero the behaviour is the pre-throttle one."""
    monkeypatch.setattr(pane_mod, "STREAM_REPAINT_S", 0.0)
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(4):
            pane._on_core_event(pane._core, AssistantText(f"tok{i} "))
        await pilot.pause()

        assert _painted(pane) == _recorded(pane) == "tok0 tok1 tok2 tok3 "
        assert pane._repaint_pending is False


@pytest.mark.asyncio
async def test_end_of_stream_never_leaves_the_widget_stale(monkeypatch):
    """Whatever the throttle skipped, the settled block still shows it."""
    monkeypatch.setattr(pane_mod, "STREAM_REPAINT_S", 3600.0)
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(4):
            pane._on_core_event(pane._core, AssistantText(f"tok{i} "))
        await pilot.pause()
        block = pane._streaming_block

        pane._flush_streaming()
        await pilot.pause()

        assert block.text_payload() == "tok0 tok1 tok2 tok3 "
        assert pane._repaint_pending is False


@pytest.mark.asyncio
async def test_thinking_stream_is_reconciled_too(monkeypatch):
    """The thinking branch has no Markdown re-render on flush, so the
    reconcile is the only thing that catches it up."""
    from aegis.events import AssistantThinking

    monkeypatch.setattr(pane_mod, "STREAM_REPAINT_S", 3600.0)
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(4):
            pane._on_core_event(pane._core, AssistantThinking(f"mull{i} "))
        await pilot.pause()
        block = pane._streaming_block
        recorded = _recorded(pane)

        pane._flush_streaming()
        await pilot.pause()

        assert block.text_payload() == recorded
        assert pane._repaint_pending is False
