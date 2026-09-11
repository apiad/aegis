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


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    mgr = SessionManager({"default": object()}, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


async def test_two_views_are_two_apps_over_one_manager(tmp_path):
    reg, mgr = _reg(tmp_path)
    a = await reg.open("tty-1", (80, 24))
    b = await reg.open("web-1", (140, 50))
    assert a.app is not b.app
    assert a.app.manager is mgr and b.app.manager is mgr
    await reg.close_all()


async def test_reopening_a_live_id_returns_the_same_view(tmp_path):
    """A reconnect must not build a second app for one client."""
    reg, _ = _reg(tmp_path)
    a = await reg.open("tty-1", (80, 24))
    again = await reg.open("tty-1", (80, 24))
    assert again is a
    await reg.close_all()


async def test_closing_one_view_leaves_the_other(tmp_path):
    reg, _ = _reg(tmp_path)
    await reg.open("tty-1", (80, 24))
    await reg.open("web-1", (140, 50))
    await reg.close("tty-1")
    assert reg.list() == ["web-1"]
    await reg.close_all()


async def test_closing_a_view_persists_its_state(tmp_path):
    """A view outlives its socket. Reattaching restores focus and drafts."""
    from aegis.views.state import load_view
    reg, _ = _reg(tmp_path)
    v = await reg.open("tty-1", (80, 24))
    v.state.drafts["lucid-knuth"] = "half typed"
    await reg.close("tty-1")
    roots = AegisRoots.for_project(tmp_path)
    assert load_view(roots.state_dir, "tty-1").drafts == {
        "lucid-knuth": "half typed"}
