"""A tab adopted from the brain must come back with its conversation.

`_mount_brain_pane` builds its pane with `core=session` and passes no
`replay=` and no `log_id=`. Every other construction site passes
`replay=_safe_replay(state_dir, log_id)`. For a session being spawned
right now that omission is invisible, because there is nothing to replay.

On a reattach it is the whole problem. The daemon keeps the session, the
view is rebuilt from scratch, and the tab returns with an empty
transcript: the handle is right, the status bar is right, and everything
that was said is gone. From the user's side the daemon did not reload what
was running.

Asserted on the blocks mounted in the transcript rather than on the
screen, so a scroll position or a narrow terminal cannot make it pass.
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result, SystemInit
from aegis.tui.pane import ConversationPane
from aegis.views.registry import ViewRegistry
from textual.widgets import ContentSwitcher

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        yield SystemInit(session_id="sid-1")
        yield AssistantText("the answer from before the detach", usage=None)
        yield Result(duration_ms=1, is_error=False)


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _registry(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"opus": _agent()}
    mgr = SessionManager(roster, "opus",
                         make_session=lambda p, u, h, **kw: _FakeHarness(),
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    return reg, mgr, roots


def _transcript_blocks(app, handle) -> int:
    pane = next(p for p in app.query(ConversationPane) if p.handle == handle)
    return len(pane.query_one("#transcript").children)


async def test_a_reattached_view_gets_the_transcript_back(tmp_path):
    from aegis.state.session_log import append_event

    reg, mgr, roots = _registry(tmp_path)

    first = await reg.open("tty-1", (100, 30))
    async with first.app.run_test(headless=False, size=(100, 30)) as pilot:
        handle = await mgr.spawn("opus")
        await pilot.pause()
        # The conversation that happened before the detach. Written to the
        # session log, which is where a replay reads it from and what the
        # daemon still holds when the view is rebuilt.
        log_id = mgr.get(handle).log_id
        for ev in (SystemInit(session_id="sid-1"),
                   AssistantText("the answer from before the detach",
                                 usage=None),
                   Result(duration_ms=1, is_error=False)):
            append_event(roots.state_dir, log_id, ev)
        await pilot.pause()
    await reg.close("tty-1")

    # The daemon kept the session; the view is built fresh, as it is after
    # a detach or a client that went away.
    # The boot also spawns a default session, so this is a membership
    # check rather than an equality one.
    assert handle in [s.handle for s in mgr.list_sessions()], \
        "the brain lost the session across the view rebuild"
    second = await reg.open("tty-1", (100, 30))
    try:
        async with second.app.run_test(headless=False, size=(100, 30)) as p2:
            await p2.pause()
            # Boot mounts every adopted tab hidden and shows one, and a
            # hidden pane defers its replay to on_show so boot stays O(1)
            # in tab count. Bring this one forward, which is what the user
            # does, and the replay must be there when they look.
            second.app.query_one(ContentSwitcher).current = f"pane-{handle}"
            await p2.pause()
            await p2.pause()
            after = _transcript_blocks(second.app, handle)
            assert after, (
                "the reattached tab came back with an empty transcript; "
                "the conversation on disk was not replayed into it")
    finally:
        await reg.close_all()
