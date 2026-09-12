"""A View is one AegisApp bound to one sink at one geometry. The app holds
the brain by direct reference through bridge= (stage 3), so there is no
protocol between the view and the manager."""
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.state import ViewState, save_view
from aegis.views.view import open_view

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr(roots):
    return SessionManager({"default": object()}, "default",
                          make_session=lambda p, u, h: _FakeHarness(),
                          mcp=None, roots=roots)


async def test_view_holds_the_manager_by_reference(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    mgr = _mgr(roots)
    v = await open_view("tty-1", manager=mgr, geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
    assert v.app.manager is mgr
    await v.stop()


async def test_view_restores_persisted_state(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    save_view(roots.state_dir, ViewState("tty-1", (140, 50), "lucid-knuth",
                                         {"lucid-knuth": 12},
                                         {"lucid-knuth": "half typed"}))
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(140, 50),
                        roots=roots, mcp=FakeMCP())
    assert v.state.drafts == {"lucid-knuth": "half typed"}
    assert v.state.active_handle == "lucid-knuth"
    await v.stop()


async def test_a_first_attach_has_no_prior_state(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    v = await open_view("brand-new", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
    assert v.state.drafts == {}
    assert v.state.geometry == (80, 24)
    await v.stop()


async def test_geometry_comes_from_this_attach_not_the_last(tmp_path):
    """A terminal can be resized between two attaches.

    Every other geometry test here opens at the SAME size it persisted, so
    none of them can see `state.geometry = geometry` removed — the restored
    value and the requested one are identical and the line is invisible.
    This one opens narrow over a wide persisted state.
    """
    roots = AegisRoots.for_project(tmp_path)
    save_view(roots.state_dir, ViewState("tty-1", (140, 50), "lucid-knuth"))
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
    assert v.state.geometry == (80, 24), (
        "the view came back at its previous size, not the one it attached "
        "at — a resized terminal would render at the old geometry")
    # The rest of the restored state must survive that override.
    assert v.state.active_handle == "lucid-knuth"
    await v.stop()


async def test_run_hands_the_views_geometry_to_its_driver(tmp_path):
    """`run()` must pass size= explicitly.

    run_async defaults size to None, which reaches the driver as None and
    falls through to the COLUMNS/ROWS env vars (web_driver.py:52-59) — the
    process-global this whole stage exists to remove. Asserted on the live
    driver, not on ViewState: a geometry that never leaves the dataclass is
    a write-only field.
    """
    import asyncio
    monkey_free_roots = AegisRoots.for_project(tmp_path)
    v = await open_view("tty-1", manager=_mgr(monkey_free_roots),
                        geometry=(140, 50), roots=monkey_free_roots,
                        mcp=FakeMCP())
    await v.run()
    try:
        for _ in range(100):          # let the app reach driver construction
            await asyncio.sleep(0.01)
            if getattr(v.app, "_driver", None) is not None:
                break
        assert v.app._driver is not None, "the app never built a driver"
        assert v.app._driver._size == (140, 50), (
            f"driver got {v.app._driver._size}, not the view's geometry")
    finally:
        await v.stop()


async def test_the_app_holds_the_same_view_state_object(tmp_path):
    """Restored state must reach the app, or it is write-only.

    The app reads focus off its ViewState on resume (app.py:709) and writes
    focus back into it on every tab change. If open_view kept the restored
    state to itself, a reattach would restore nothing and persist nothing
    the app had done — the field would look wired and be inert.
    """
    roots = AegisRoots.for_project(tmp_path)
    save_view(roots.state_dir, ViewState("tty-1", (80, 24), "lucid-knuth"))
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
    assert v.app._view_state is v.state
    assert v.app._view_state.active_handle == "lucid-knuth"
    await v.stop()


async def test_geometry_is_the_drivers_and_not_the_environments(tmp_path,
                                                                monkeypatch):
    """COLUMNS/ROWS is process-global (web_driver.py:52-59). A view's size
    must come from its own argument or N views share one geometry."""
    monkeypatch.setenv("COLUMNS", "999")
    monkeypatch.setenv("ROWS", "999")
    roots = AegisRoots.for_project(tmp_path)
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
    assert v.state.geometry == (80, 24)
    await v.stop()
