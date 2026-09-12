"""Tab identity is brain state: a session that exists, exists for everyone.

Focus, scroll and drafts stay per-view -- that is the stage-4 split and it
stands. Only which tabs EXIST crosses.
"""
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry

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


def _reg(tmp_path):
    """A registry over one brain, with an agent roster on both halves."""
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h, **kw: _FakeHarness(),
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    return reg, mgr


def _handles(app):
    return {p.handle for p in app._panes if hasattr(p, "handle")}


async def test_a_session_spawned_on_the_brain_reaches_every_view(tmp_path):
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            h = await mgr.spawn("default")
            await pa.pause()
            await pb.pause()
            assert h in _handles(a.app), f"view A never saw {h}"
            assert h in _handles(b.app), f"view B never saw {h}"
    await reg.close_all()


async def test_both_views_hold_the_manager_s_session_not_a_copy(tmp_path):
    """The identity that matters. Two panes over two DIFFERENT AgentSessions
    wrapping one harness would satisfy a handle-equality check and be a
    different, broken thing: two transcripts, two pending queues, and a
    message cancelled in one view still live in the other."""
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            h = await mgr.spawn("default")
            await pa.pause()
            await pb.pause()
            pane_a = next(p for p in a.app._panes
                          if getattr(p, "handle", None) == h)
            pane_b = next(p for p in b.app._panes
                          if getattr(p, "handle", None) == h)
            assert pane_a is not pane_b, "one pane cannot live in two apps"
            assert pane_a._core is mgr.get(h)
            assert pane_b._core is mgr.get(h)
    await reg.close_all()


async def test_a_view_attaching_late_sees_the_sessions_already_there(tmp_path):
    """The daemon case: the brain outlives its views, so a client attaching
    to a running aegis must find the tabs that are already open rather than
    an empty window."""
    reg, mgr = _reg(tmp_path)
    h = await mgr.spawn("default")
    late = await reg.open("late", (80, 24))
    async with late.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        assert h in _handles(late.app), (
            f"a view attaching to a brain with {h} open saw "
            f"{sorted(_handles(late.app))}")
    await reg.close_all()


async def test_the_loop_chip_and_recap_reach_every_view(tmp_path):
    """`on_loop` and `on_recap` were single slots on AgentSession, assigned
    by the pane (`pane.py:880`, `:885`) on the stated assumption that "one
    frontend owns the chip". With N panes over one session the second
    assignment silently replaces the first, so the loop chip and the
    end-of-turn recap would render only in the last view to attach — and
    nothing would report it, because no exception is raised.

    Asserted on the core's own notification, not on a widget: the point is
    that the session fans out to every subscriber.
    """
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            h = await mgr.spawn("default")
            await pa.pause()
            await pb.pause()
            core = mgr.get(h)

            loops, recaps = [], []
            core.add_loop_observer(lambda c, lp, reason: loops.append(reason))
            core.add_loop_observer(lambda c, lp, reason: loops.append(reason))
            core._emit_loop("test")
            assert loops == ["test", "test"], (
                "a second loop observer replaced the first instead of "
                "joining it")

            from types import SimpleNamespace
            core.add_recap_observer(lambda c, r: recaps.append(r))
            core.add_recap_observer(lambda c, r: recaps.append(r))
            core._emit_recap(SimpleNamespace(line="a line", ok=True))
            assert len(recaps) == 2, (
                "a second recap observer replaced the first")

            # And both panes are really registered, not just capable of it.
            pane_a = next(p for p in a.app._panes
                          if getattr(p, "handle", None) == h)
            pane_b = next(p for p in b.app._panes
                          if getattr(p, "handle", None) == h)
            registered = set(core._loop_observers)
            assert pane_a._on_loop_change in registered
            assert pane_b._on_loop_change in registered
    await reg.close_all()


async def test_a_tab_opened_in_one_view_appears_in_the_other(tmp_path):
    """The user-visible property, driven through the real Ctrl+N action
    rather than the manager.

    Spawning on the manager (the tests above) proves propagation; this
    proves the app actually USES it. Before this, action_new_tab went to
    the app's own local plane and the tab could not leave the view it was
    opened in.
    """
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            await pa.pause()
            await pb.pause()
            before = _handles(b.app)
            await a.app.action_new_tab()
            await pa.pause()
            await pb.pause()
            new = _handles(b.app) - before
            assert new, (
                f"Ctrl+N in view A added no tab to view B "
                f"(B still has {sorted(before)})")
            assert new <= _handles(a.app), "the tab is missing from view A"
    await reg.close_all()


async def test_the_app_and_its_brain_share_one_handle_registry(tmp_path):
    """Handles are brain state. Two registries over one session set means
    two views can mint the SAME name -- the manager believes one session
    exists and the second pane collides on id=f"pane-{handle}".

    Asserted as object identity plus a behavioural check, because two
    registries that merely happen to agree today would satisfy identity
    alone in a future where one of them is rebuilt.
    """
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            assert a.app._handles is mgr.handles
            assert b.app._handles is mgr.handles
            h = await mgr.spawn("default")
            await pa.pause()
            await pb.pause()
            assert mgr.handles.owner(h) is not None
            # A name the brain has bound cannot be minted again by a view.
            assert a.app._mint_handle(None) != h
    await reg.close_all()


async def test_closing_on_the_brain_drops_the_pane_everywhere(tmp_path):
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            h = await mgr.spawn("default")
            await pa.pause()
            await pb.pause()
            assert h in _handles(a.app) and h in _handles(b.app)
            await mgr.close(h)
            await pa.pause()
            await pb.pause()
            assert h not in _handles(a.app), "view A kept a dead tab"
            assert h not in _handles(b.app), "view B kept a dead tab"
    await reg.close_all()
