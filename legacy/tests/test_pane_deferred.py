"""ConversationPane: a deferred command runs OFF the input handler.

`/btw` takes 12-17s and `@peer` up to `PEER_ASK_TIMEOUT_S = 300`. Awaiting
either inside `on_growing_input_submitted` — a Textual message handler —
holds this pane's whole message pump for the duration: no working
indicator, no tool spinners, no input. The placeholder block is what fills
the gap once the await moves to a worker.

The bridge here blocks on an `asyncio.Event`, so "still running" is a
deterministic state rather than a timing race.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.btw import SideNote
from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    def __init__(self):
        self.sent: list[str] = []
        self._gate = asyncio.Event()

    async def start(self):
        pass

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def interrupt(self):
        pass

    async def close(self):
        pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


class SlowBridge:
    """An AegisApp whose `side_note` blocks until released."""

    def __init__(self, app):
        self._app = app
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = 0

    async def side_note(self, handle, prompt):
        self.calls += 1
        self.started.set()
        await self.gate.wait()
        return SideNote(answer="from the window", ok=True, model="haiku",
                        header="last 6 of 47 turns", duration_ms=5200,
                        cost_usd=0.0044)

    def __getattr__(self, name):
        return getattr(self._app, name)


def _app(session):
    def make(agent, mcp_url, handle):
        return session
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


async def _submit(pane, text):
    inp = pane.query_one(GrowingInput)
    await pane.on_growing_input_submitted(
        GrowingInput.Submitted(inp, text, "enqueue"))


# ---------- the freeze is gone -------------------------------------------

@pytest.mark.asyncio
async def test_the_input_handler_returns_before_the_note_does():
    """The whole point. If the handler awaited the note, this `wait_for`
    would time out and every spinner in the pane would be frozen for the
    12-17s the call takes."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await asyncio.wait_for(_submit(pane, "/btw which path?"), timeout=1.0)
        await pilot.pause()
        assert pane._deferred is not None
        assert not pane._deferred.done
        bridge.gate.set()


@pytest.mark.asyncio
async def test_the_note_is_never_delivered_to_the_agent():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        assert sess.sent == []
        bridge.gate.set()


@pytest.mark.asyncio
async def test_a_placeholder_is_mounted_immediately_with_the_question():
    """A spinner with no subject is just anxiety — by second twelve you
    have forgotten which side question you asked."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path does resume take?")
        await pilot.pause()
        payload = pane._history[pane._deferred.idx].payload
        assert "btw" in payload
        assert "which path does resume take?" in payload
        bridge.gate.set()


@pytest.mark.asyncio
async def test_the_answer_replaces_the_placeholder_in_place():
    """In place, at the index the command mounted — so a note stays where
    you asked it while the agent's output streams past underneath."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        idx = pane._deferred.idx
        before = len(pane._history)
        bridge.gate.set()
        for _ in range(50):
            await pilot.pause()
            if pane._deferred is None:
                break
        assert pane._deferred is None
        assert "from the window" in pane._history[idx].payload
        assert len(pane._history) == before, "the answer appended a block"


# ---------- one at a time ------------------------------------------------

@pytest.mark.asyncio
async def test_a_second_btw_is_refused_while_one_is_running():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw first question")
        await pilot.pause()
        first = pane._deferred
        await _submit(pane, "/btw second question")
        await pilot.pause()
        assert pane._deferred is first, "the second displaced the first"
        assert bridge.calls == 1, "the second question was actually asked"
        assert "already running" in pane._history[-1].payload
        bridge.gate.set()


@pytest.mark.asyncio
async def test_an_ordinary_command_still_works_while_a_note_runs():
    """Only a second *deferred* command is refused. Replacing a freeze
    with a lock would be no improvement."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw a question")
        await pilot.pause()
        await _submit(pane, "/sessions")
        await pilot.pause()
        assert pane._deferred is not None
        assert "already running" not in pane._history[-1].payload
        bridge.gate.set()


@pytest.mark.asyncio
async def test_a_normal_message_still_reaches_the_agent_while_a_note_runs():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw a question")
        await pilot.pause()
        await _submit(pane, "carry on please")
        await pilot.pause()
        assert sess.sent == ["carry on please"]
        bridge.gate.set()


@pytest.mark.asyncio
async def test_a_second_btw_after_the_first_landed_is_allowed():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw first")
        await pilot.pause()
        bridge.gate.set()
        for _ in range(50):
            await pilot.pause()
            if pane._deferred is None:
                break
        bridge.gate.clear()
        await _submit(pane, "/btw second")
        await pilot.pause()
        assert pane._deferred is not None
        assert bridge.calls == 2
        bridge.gate.set()


# ---------- the ticker ---------------------------------------------------

@pytest.mark.asyncio
async def test_a_turn_ending_does_not_freeze_a_running_note():
    """/btw reads the log, not the session — that independence is why it
    is legal mid-turn, so a turn finishing underneath one must leave its
    spinner ticking. _freeze_all_tools used to stop the ticker flat."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        pane._freeze_all_tools()
        assert pane._any_spinner_running()
        assert pane._tool_timer is not None, "the note's spinner froze"
        bridge.gate.set()


# ---------- ESC cancels --------------------------------------------------

@pytest.mark.asyncio
async def test_esc_cancels_a_running_note_and_leaves_a_tombstone():
    """A tombstone rather than a removal: ESC silently deleting something
    you can see reads as a glitch, and the block is the only record that
    you spent anything at all."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        idx = pane._deferred.idx
        assert pane.cancel_deferred_if_running() is True
        assert pane._deferred is None
        assert "cancelled" in pane._history[idx].payload
        bridge.gate.set()


@pytest.mark.asyncio
async def test_esc_reports_not_consumed_when_no_note_is_running():
    """So the app falls through to clear-input, then interrupt."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test():
        pane = app._panes[0]
        assert pane.cancel_deferred_if_running() is False


@pytest.mark.asyncio
async def test_a_cancelled_note_that_lands_late_is_dropped():
    """A side question must never disturb the conversation it sits beside,
    and that includes on the way out."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        idx = pane._deferred.idx
        pane.cancel_deferred_if_running()
        bridge.gate.set()
        for _ in range(50):
            await pilot.pause()
        assert "cancelled" in pane._history[idx].payload
        assert "from the window" not in pane._history[idx].payload


@pytest.mark.asyncio
async def test_a_new_note_is_allowed_after_a_cancel():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw first")
        await pilot.pause()
        pane.cancel_deferred_if_running()
        await _submit(pane, "/btw second")
        await pilot.pause()
        assert pane._deferred is not None
        assert pane._deferred.subject == "second"
        bridge.gate.set()


@pytest.mark.asyncio
async def test_the_ticker_stops_when_the_cancelled_note_was_the_last_spinner():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        assert pane._tool_timer is not None
        pane.cancel_deferred_if_running()
        assert pane._tool_timer is None
        bridge.gate.set()


# ---------- the ESC ladder -----------------------------------------------

@pytest.mark.asyncio
async def test_esc_cancels_the_note_before_it_clears_a_half_typed_line():
    """The rung order. The spinning block is the live thing on screen and
    it is billing by the second; clearing the input is reachable by other
    means and interrupting the turn is the destructive option."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bridge = SlowBridge(app)
        pane.app.side_note = bridge.side_note
        await _submit(pane, "/btw which path?")
        await pilot.pause()
        pane.query_one(GrowingInput).value = "half typed"
        app.action_interrupt()
        assert pane._deferred is None, "the note survived"
        assert pane.query_one(GrowingInput).value == "half typed", \
            "the input was cleared instead of the note being cancelled"
        bridge.gate.set()


@pytest.mark.asyncio
async def test_esc_clears_the_input_when_no_note_is_running():
    """The rung below still works — cancelling is inserted, not swapped."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test():
        pane = app._panes[0]
        pane.query_one(GrowingInput).value = "half typed"
        app.action_interrupt()
        assert pane.query_one(GrowingInput).value == ""


@pytest.mark.asyncio
async def test_esc_interrupts_the_turn_when_nothing_else_claims_it():
    """The bottom rung: no modal, no note, no half-typed line."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test():
        pane = app._panes[0]
        calls = []
        pane.interrupt = lambda *a, **k: calls.append(1)
        app.action_interrupt()
        assert calls == [1]


# ---------- both spellings reach the deferred path -----------------------

@pytest.mark.parametrize("typed,expected_verb", [
    ("/peer beta is the build green?", "peer"),
    ("@beta is the build green?", "peer"),
    ("/btw which path?", "btw"),
])
@pytest.mark.asyncio
async def test_every_spelling_reaches_the_deferred_path(typed, expected_verb):
    """`@beta …` has no verb until classify_input rewrites it to
    `/peer beta …`, so the pane must classify FIRST and resolve on the
    payload. Resolving the raw line returns None for every `@` spelling and
    drops it silently back onto the inline-await path — the 300s freeze,
    reappearing on one spelling while `/peer` works perfectly.

    A test on the slash spelling alone passes with that bug present.
    """
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        seen = []
        real = pane._start_deferred
        pane._start_deferred = lambda payload, cmd, args, width: seen.append(
            (payload, cmd.name))
        await _submit(pane, typed)
        await pilot.pause()
        pane._start_deferred = real
        assert seen, f"{typed!r} never reached the deferred path"
        assert seen[0][1] == expected_verb
        assert sess.sent == [], f"{typed!r} was delivered to the agent"


@pytest.mark.asyncio
async def test_esc_offers_the_key_to_the_active_tab_before_the_pane_rungs():
    """A file browser / file tab has no input and no turn, but it does own
    escape (leave edit mode, go back to browse). It claims the key through
    `escape_handled`, which sits above the whole pane ladder — and when it
    declines, the rungs below still run."""
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(GrowingInput).value = "half typed"

        target = Path.cwd() / "escape_target.py"
        target.write_text("x = 1")
        await app.action_open_file_picker()
        await pilot.pause()
        tab = app._panes[-1]
        assert app._active is tab
        await tab._switch_to_view(target)
        await pilot.pause()
        assert tab.query_one("#fb-view").display is True

        app.action_interrupt()
        await pilot.pause()
        assert tab.query_one("#fb-browse").display is True, \
            "escape never reached the browser tab"
        assert pane.query_one(GrowingInput).value == "half typed", \
            "the tab claimed escape but a lower rung ran anyway"

        # Browse mode has nothing to go back to and declines, leaving the
        # key to the rungs below (which do nothing here — the ladder acts
        # on `_active`, and `_active` is the browser tab, not the pane).
        assert tab.escape_handled() is False
        app.action_interrupt()
        await pilot.pause()
        assert tab.query_one("#fb-browse").display is True
