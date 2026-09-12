"""The stage-4 gate. Two views, one brain.

Deliberately in-process: each view's frames are read out of its own sink,
so a failure here is the seam and never a socket. The transport arrives in
stage 5 and inherits this contract.
"""
import pytest

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


def _reg(tmp_path, *, agents=False):
    """A registry over one brain.

    ``agents=True`` also gives each view an agent roster and a session
    factory, so its app mounts a real ConversationPane on boot. Only the
    tests that need a pane to type into ask for it — the geometry and sink
    tests are about the driver and want the cheapest possible app.
    """
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    app_kw = {}
    if agents:
        app_kw = dict(agents=roster, default_agent="default",
                      make_session=lambda p, u, h: _FakeHarness())
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                        **app_kw), mgr


def _assert_live_driver_is_ours(app):
    """run_test(headless=True) swaps in HeadlessDriver (app.py:3334-3337).

    Without this guard the frame assertions below pass vacuously — an empty
    sink because no ViewDriver ran reads identically to an empty sink
    because frames did not cross, and the second is the only one this gate
    is about.
    """
    from aegis.views.driver import ViewDriver
    assert isinstance(app._driver, ViewDriver), (
        f"view is running on {type(app._driver).__name__}, not ViewDriver — "
        "pass headless=False to run_test")


async def _settle(pilot, view, *, rounds=40):
    """Pump the app until its sink stops growing.

    Returns once two consecutive pauses add no frames, so the caller can
    attribute later frames to what it does next rather than to the boot
    render still draining.
    """
    stable = 0
    for _ in range(rounds):
        before = len(view.frames)
        await pilot.pause()
        stable = stable + 1 if len(view.frames) == before else 0
        if stable >= 2:
            return
    raise AssertionError(
        f"view {view.view_id} never stopped rendering after {rounds} pauses")


async def test_both_views_hold_their_own_geometry(tmp_path):
    reg, _ = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)):
        async with b.app.run_test(headless=False, size=(140, 50)):
            _assert_live_driver_is_ours(a.app)
            _assert_live_driver_is_ours(b.app)
            assert a.app.size == (80, 24)
            assert b.app.size == (140, 50)
    await reg.close_all()


@pytest.mark.xfail(strict=True, reason=(
    "UNBUILT: nothing propagates the brain's session set into a view's pane "
    "list. AegisApp is its own AppBridge on the local plane (app.py:472-478) "
    "and spawns through _SessionManagerAdapter(self), so `bridge=` supplies "
    "roots and handles but never panes. Measured 2026-09-11: two views over "
    "one SessionManager mount two DIFFERENT default tabs, and a session "
    "spawned on the manager reaches neither. The remote plane has the "
    "equivalent wiring (_on_remote_session_list, app.py:2058) and the local "
    "plane has no counterpart. strict=True so whoever builds it must come "
    "back and delete this marker."))
async def test_a_session_opened_in_one_view_appears_in_the_other(tmp_path):
    """Tab identity is brain state: opening a tab opens it for everyone.

    Asserts the tab reaches the OTHER view's pane set. `a.app.manager is
    b.app.manager` — which an earlier draft asserted — is reference
    identity between two attributes and says nothing about whether either
    view ever rendered the tab.

    Spawns on the MANAGER, not on one view's app: "opened in one view" means
    the view asked the brain, which is the only route by which a second view
    could ever learn of it. Spawning through `a.app.spawn` exercises A's own
    local plane and could never reach B by construction.
    """
    reg, mgr = _reg(tmp_path, agents=True)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            _assert_live_driver_is_ours(a.app)
            _assert_live_driver_is_ours(b.app)
            handle = await mgr.spawn("default")
            await pa.pause()
            await pb.pause()
            b_handles = {p.handle for p in b.app._panes
                         if hasattr(p, "handle")}
            assert handle in b_handles, (
                f"tab {handle} opened in view A never reached view B: "
                f"{sorted(b_handles)}")
    await reg.close_all()


async def test_focus_and_drafts_do_not_cross(tmp_path):
    """Typing into one view must not appear in another.

    Drives the real input widget rather than assigning to two ViewState
    dataclasses. An earlier draft did the latter and asserted the other's
    `field(default_factory=dict)` defaults — which would have passed even
    if both views shared a single AegisApp, i.e. it tested dataclasses and
    not the seam at all.
    """
    reg, _ = _reg(tmp_path, agents=True)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)) as pb:
            _assert_live_driver_is_ours(a.app)
            _assert_live_driver_is_ours(b.app)
            # Each view boots its own ConversationPane, so each has an input
            # to type into.
            await pa.pause()
            await pb.pause()
            a_input = a.app.query_one("GrowingInput")
            b_input = b.app.query_one("GrowingInput")
            assert a_input is not b_input, (
                "both views resolved to one input widget — they are sharing "
                "an AegisApp, and every other assertion here is meaningless")
            await pa.press(*"typed in A")
            await pa.pause()
            await pb.pause()
            assert "typed in A" in a_input.text
            assert b_input.text == "", (
                f"view B sees view A's draft: {b_input.text!r}")
    await reg.close_all()


async def test_each_view_writes_only_to_its_own_sink(tmp_path):
    """The property the whole seam rests on. If frames cross, every other
    assertion here is coincidence."""
    reg, _ = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)):
        _assert_live_driver_is_ours(a.app)
    assert a.frames, "view A rendered nothing"
    assert not b.frames, "view B received view A's frames"
    await reg.close_all()


async def test_a_reattached_view_gets_a_full_frame(tmp_path):
    """Textual emits incremental updates (_compositor.py:1118), so a client
    reattaching to a live view would otherwise receive deltas against a
    screen it has never seen."""
    reg, _ = _reg(tmp_path)
    v = await reg.open("tty-1", (80, 24))
    async with v.app.run_test(headless=False, size=(80, 24)) as pilot:
        _assert_live_driver_is_ours(v.app)
        # Let the app go quiet FIRST. Without this the boot render is still
        # in flight, `pause()` below flushes it, and every frame counted
        # afterwards arrives whether repaint() did anything or not — the
        # assertion then passes with repaint() as a literal `return`
        # (measured 2026-09-11). Quiescing makes any frame seen below
        # attributable to the repaint and nothing else.
        await _settle(pilot, v)
        v.frames.clear()
        v.repaint()
        await pilot.pause()
        # Read the frames INSIDE the context: exiting run_test stops
        # application mode, which writes the alt-screen teardown and an
        # exit meta frame — more bytes that are not a repaint.
        emitted = list(v.frames)
    assert emitted, "repaint() emitted no frame"
    v.frames[:] = emitted
    # A partial update is also "a frame". What distinguishes a full repaint
    # is that it addresses every row, so count cursor-position sequences
    # (CSI row;col H) rather than escape bytes -- `\x1b[` also matches every
    # colour and style run, which a one-line delta has plenty of. Measured
    # on a quiesced 80x24 view: a full repaint is 25 CUP sequences.
    import re
    payload = b"".join(f[5:] for f in v.frames)   # strip b"D" + 4-byte len
    rows = len(re.findall(rb"\x1b\[\d+;\d+H", payload))
    assert rows >= v.state.geometry[1], (
        f"repaint addressed {rows} rows of a {v.state.geometry[1]}-row "
        f"view — that is a partial update, not a full frame")
    await reg.close_all()
