"""A session that renames itself must say so in the tab.

There are two rename implementations. `AegisApp`'s own updates the pane it
is holding; `SessionManager.rename_handle` updates the handle registry,
the MRU, the inbox, the locks, the MCP token, the monitors and the
reminders, and tells nobody. The manager's is the one that runs under the
daemon, because there the brain is the bridge the `aegis_rename` tool
reaches.

So an agent renamed itself, the tool answered `{"ok": true}`, every plane
keyed by handle moved, and the tab bar kept the old name. Alex saw it
live.

The pane is found by identity rather than by handle: after the rename the
session no longer answers to the name the view knows it by, so looking it
up by either name is wrong in one direction or the other. The DOM id
stays put, which is deliberate and is the whole reason HandleRegistry
never frees a name.
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.tui.pane import ConversationPane
from aegis.views.registry import ViewRegistry

from tests.brain import make_brain
from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


async def test_a_rename_on_the_brain_reaches_every_view(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"opus": _agent()}
    mgr = make_brain(roster, "opus",
                         make_session=lambda p, u, h, **kw: _FakeHarness(),
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="opus",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    a = await reg.open("tty-a", (100, 30))
    b = await reg.open("tty-b", (100, 30))
    try:
        async with a.app.run_test(headless=False, size=(100, 30)) as pa:
            async with b.app.run_test(headless=False, size=(100, 30)) as pb:
                old = await mgr.spawn("opus")
                await pa.pause()
                await pb.pause()
                assert old in [p.handle for p in a.app.query(ConversationPane)]

                res = await mgr.rename_handle(old, "lucid-lamport")
                assert res.get("ok"), res
                await pa.pause()
                await pb.pause()

                for name, app in (("a", a.app), ("b", b.app)):
                    handles = [p.handle for p in app.query(ConversationPane)]
                    assert "lucid-lamport" in handles, (
                        f"view {name} still shows {handles}; the rename "
                        "never reached it")
                    assert old not in handles

                # The DOM id keeps the birth name on purpose: it is what
                # makes renaming back into a former name safe.
                assert a.app.query_one(f"#pane-{old}", ConversationPane)
    finally:
        await reg.close_all()
