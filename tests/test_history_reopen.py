"""Reopening a conversation from Ctrl+R must work for the life of the process.

`HandleRegistry` never frees a handle — by design, because a pane's Textual
DOM id is the handle it was *born* with and re-minting a live id is
`DuplicateIds`. `_resume_from_history` enforced that with
`self._handles.owner(tab.handle) is not None`, which asks a question one
size too big: "was this name ever bound?" rather than "is a pane holding
that DOM id right now?".

For a TUI that dies with the terminal the difference never showed. Under
`aegis serve` the process lives for days, so the first tab you close
retires its handle forever and Ctrl+R → select answers "cannot reopen …
a live tab already holds that handle" with no live tab anywhere. Every
closed session makes another slice of history unreachable.

The reopen is safe precisely because the pane that held the id is gone from
the DOM. What is NOT safe is a live pane that was *renamed* away from its
birth handle: `p.handle` no longer matches, but `#pane-<birth>` is still
mounted. That case must still be refused.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, SystemInit
from aegis.state.session_log import replay_events
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane


def _agent():
    return Agent(harness="claude-code", model="claude-sonnet-4-5",
                 effort="medium", permission="auto")


class FakeSession:
    def __init__(self, session_id: str = "sid-1"):
        self.sent: list[str] = []
        self.session_id = session_id

    async def start(self): pass
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield SystemInit(session_id=self.session_id)
        yield AssistantText("ok", usage=None)
        yield Result(duration_ms=1, is_error=False)
    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


class FakeDriver:
    name = "claude-code"
    supports_resume = True

    def __init__(self):
        self.resumed: list[tuple[str, str | None]] = []

    def resume(self, agent, cwd, mcp_url, handle, session_id):
        self.resumed.append((handle, session_id))
        self.session = FakeSession(session_id or "sid-1")
        return self.session


def _app(driver: FakeDriver) -> AegisApp:
    return AegisApp({"sonnet": _agent()}, "sonnet",
                    lambda *a, **kw: FakeSession(), FakeMCP(),
                    drivers={"claude-code": driver})


def _row_for(app: AegisApp, handle: str):
    import aegis.state.history as history_mod
    rows = history_mod.list_history(app._state_dir, live_handles=set())
    return next(r for r in rows if r.handle == handle)


@pytest.mark.asyncio
async def test_a_closed_conversation_reopens_and_accepts_a_message(
        tmp_path: Path, monkeypatch):
    """The whole Ctrl+R contract: pick a closed session, get it back, talk
    to it. Asserted on the pane and on the driver's resume call, not on the
    absence of a warning — a refusal that changed its wording would still
    have to fail this."""
    monkeypatch.chdir(tmp_path)
    driver = FakeDriver()
    app = _app(driver)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        handle = pane.handle
        pane._submit("hello world")
        await pilot.pause()

        await app._close_pane(pane)
        await pilot.pause()
        assert not [p for p in app._panes
                    if isinstance(p, ConversationPane) and p.handle == handle]

        row = _row_for(app, handle)
        await app._resume_from_history(row)
        await pilot.pause()

        reopened = [p for p in app._panes
                    if isinstance(p, ConversationPane) and p.handle == handle]
        assert reopened, "closed conversation did not reopen from history"
        assert driver.resumed == [(handle, "sid-1")], \
            "reopen did not route through driver.resume with the session id"
        # The pane is mounted, not merely in the list — this is what the
        # user means by "it loaded".
        assert app.query_one(f"#pane-{handle}", ConversationPane) is reopened[0]

        reopened[0]._submit("second message")
        await pilot.pause()
        # The substrate, not the widget. Two things have to be true for
        # this to be a REOPEN rather than a new session that happens to
        # share a name: the text reached the resumed harness, and the turn
        # it produced was appended to the SAME transcript — past the
        # SessionClosed marker the close wrote.
        assert driver.session.sent == ["second message"]
        assert reopened[0].log_id == row.log_id, \
            "reopen started a new transcript instead of continuing the old one"
        from aegis.events import Result, SessionClosed
        events = replay_events(app._state_dir, row.log_id).events
        kinds = [type(e).__name__ for e in events]
        assert SessionClosed.__name__ in kinds
        closed_at = kinds.index(SessionClosed.__name__)
        assert any(isinstance(e, Result) for e in events[closed_at + 1:]), \
            "no turn recorded after the reopen"


@pytest.mark.asyncio
async def test_reopen_is_refused_when_a_renamed_pane_still_holds_the_dom_id(
        tmp_path: Path, monkeypatch):
    """The collision the old guard was reaching for, kept.

    A renamed pane answers to `new`, so scanning `p.handle` misses it, but
    `#pane-<birth>` is still mounted. Reopening `birth` would be
    DuplicateIds out of the mount worker — the `/spawn` crash. Refuse.
    """
    monkeypatch.chdir(tmp_path)
    driver = FakeDriver()
    app = _app(driver)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        birth = pane.handle
        pane._submit("hello world")
        await pilot.pause()

        row = _row_for(app, birth)

        # Rename in place: handle moves, DOM id does not.
        pane.handle = "renamed-elsewhere"
        pane._core.handle = "renamed-elsewhere"
        assert app.query_one(f"#pane-{birth}", ConversationPane) is pane

        notes: list[str] = []
        app.notify = lambda msg, **kw: notes.append(msg)
        await app._resume_from_history(row)
        await pilot.pause()

        assert driver.resumed == [], "reopened over a live DOM id"
        assert notes and "cannot reopen" in notes[0]
