"""A session respawned under the handle it just closed keeps its tab.

`QueueManager.resume_task`'s cold path does exactly this: it closes the
empty session under a parked worker's handle, then `recovery.restore`
spawns the recorded conversation under the same handle. Each announcement
reaches a view as a worker, the drop for the old session and the mount for
the new one, and the mount found the old pane still standing, took it for
its own, and returned. The drop then removed it. The resumed worker ran with
no tab in any view. Issue #27.
"""
import asyncio

from textual.widgets import ContentSwitcher

from tests.views.test_ctrl_t_focus import _reg


async def test_a_session_respawned_on_its_own_handle_keeps_a_tab(tmp_path):
    reg, mgr = _reg(tmp_path)
    v = await reg.open("solo", (100, 30))
    app = v.app
    async with app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        mgr._sync_spawn("default", handle="kind-kay")
        await pilot.pause()
        assert app.pane_for("kind-kay") is not None

        # resume_task's cold path, back to back.
        await mgr.close("kind-kay")
        new = mgr._sync_spawn("default", handle="kind-kay")
        await asyncio.sleep(0.5)

        pane = app.pane_for("kind-kay")
        assert pane is not None, "the respawned worker has no tab"
        assert pane._core is new, "the tab is still the closed session's"
        ids = [p.id for p in app.query_one(ContentSwitcher).children]
        assert ids.count(pane.id) == 1
    await reg.close_all()


async def test_a_late_drop_for_the_closed_session_spares_the_new_tab(tmp_path):
    """The drop names a session, not a handle: by the time it runs, the handle
    may already belong to the respawned one."""
    reg, mgr = _reg(tmp_path)
    v = await reg.open("solo", (100, 30))
    app = v.app
    async with app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        old = mgr._sync_spawn("default", handle="kind-kay")
        await pilot.pause()
        await mgr.close("kind-kay")
        new = mgr._sync_spawn("default", handle="kind-kay")
        await asyncio.sleep(0.5)
        assert app.pane_for("kind-kay")._core is new

        await app._drop_brain_pane(old)
        await asyncio.sleep(0.2)
        pane = app.pane_for("kind-kay")
        assert pane is not None and pane._core is new
    await reg.close_all()
