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
