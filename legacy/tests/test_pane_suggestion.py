"""The pane hands the session's drafted reply to its input box."""

from aegis.tui.pane import ConversationPane
from aegis.tui.widgets import GrowingInput


class _Input:
    def __init__(self):
        self.suggestion = ""


class _Pane:
    """Only the collaborator ``_on_suggestion`` touches."""

    def __init__(self):
        self._inp = _Input()

    def input_widget(self):
        return self._inp


def test_on_suggestion_sets_the_widget():
    p = _Pane()
    ConversationPane._on_suggestion(p, None, "one call, fifth field")
    assert p._inp.suggestion == "one call, fifth field"


def test_clearing_reaches_the_widget_too():
    p = _Pane()
    p._inp.suggestion = "stale"
    ConversationPane._on_suggestion(p, None, "")
    assert p._inp.suggestion == ""


def test_on_suggestion_survives_a_pruned_pane():
    """The observer fires off Textual's dispatch, so a torn-down pane must
    not raise into the session's emit loop."""

    class _Gone:
        def input_widget(self):
            raise RuntimeError("pruned")

    ConversationPane._on_suggestion(_Gone(), None, "x")  # must not raise


def test_growing_input_is_the_real_target():
    """Guards the duck-typed panes above against the property being renamed."""
    assert isinstance(GrowingInput.suggestion, property)


def test_the_pane_releases_the_observer():
    """A detached view that kept it would go on writing into a dead box."""
    import inspect

    src = inspect.getsource(ConversationPane.release_core_observers)
    assert '"remove_suggestion_observer"' in src


# --- end to end, through the real app ---------------------------------
# The tests above are unit-level on purpose (they pin the contract), but
# none of them proves the chain actually connects. These drive the real
# AegisApp with real key events.

import asyncio  # noqa: E402

import pytest  # noqa: E402

from aegis.config import Agent  # noqa: E402
from aegis.tui.app import AegisApp  # noqa: E402


class _Session:
    def __init__(self):
        self.sent: list[str] = []
        self._gate = asyncio.Event()

    async def start(self): ...
    async def interrupt(self): ...
    async def close(self): ...

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        from aegis.events import Result

        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()


class _MCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): ...
    async def start(self): ...
    async def stop(self): ...


def _app():
    agent = Agent(
        harness="claude-code", model="opus", effort="high", permission="auto"
    )
    return AegisApp({"default": agent}, "default", lambda *a: _Session(), _MCP())


@pytest.mark.asyncio
async def test_a_suggestion_reaches_the_box_and_tab_accepts_it():
    """The whole chain: the session emits, the pane observes, the widget
    shows it dim, and a real Tab keypress fills the box."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        inp = pane.input_widget()
        inp.focus()
        await pilot.pause()

        pane._core._emit_suggestion("one call, fifth field")
        await pilot.pause()
        assert inp.placeholder == "one call, fifth field"
        assert inp.value == ""  # nothing entered the document

        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "one call, fifth field"
        assert inp.placeholder == GrowingInput.PLACEHOLDER


@pytest.mark.asyncio
async def test_typing_overwrites_the_suggestion():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        inp = pane.input_widget()
        inp.focus()
        await pilot.pause()

        pane._core._emit_suggestion("one call")
        await pilot.pause()
        await pilot.press("n", "o")
        await pilot.pause()
        assert inp.value == "no"
        # Tab now falls through to focus-next; it must not eat the typing.
        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "no"
