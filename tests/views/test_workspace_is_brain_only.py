"""Workspace is what EXISTS. Focus is what a given view is looking at, and
two views focus different tabs, so it cannot live here."""
import dataclasses

from aegis.state.workspace import Workspace


def test_workspace_has_no_active_handle():
    fields = {f.name for f in dataclasses.fields(Workspace)}
    assert "active_handle" not in fields, (
        "active_handle is view state; it belongs in ViewState. Keeping it "
        "here gives focus two sources of truth as soon as a second view "
        "exists")


def test_saved_workspace_carries_no_focus(tmp_path):
    from aegis.state.workspace import load, save
    ws = load(tmp_path) or Workspace(tabs=[], terminals=[], files=[])
    save(tmp_path, ws)
    import json
    raw = json.loads((tmp_path / "workspace.json").read_text(encoding="utf-8"))
    assert "active_handle" not in raw


async def test_resume_still_restores_the_focused_tab(tmp_path):
    """The field moved; the behaviour must not. Before this change focus
    came off Workspace.active_handle (app.py:705); it now comes off the
    view's ViewState, and a user resuming `aegis` must not be able to tell."""
    from aegis.views.state import ViewState
    vs = ViewState(view_id="tty", geometry=(80, 24), active_handle="second")
    assert vs.active_handle == "second"
    # The app must consult the view state, not the workspace.
    import inspect
    from aegis.tui.app import AegisApp
    assert "view_state" in inspect.signature(AegisApp.__init__).parameters
    src = inspect.getsource(AegisApp)
    assert "ws.active_handle" not in src, (
        "resume still reads focus off the workspace")


async def test_the_local_tui_boot_gives_its_app_a_view_state(tmp_path):
    """`aegis` is the single-view path this plan must not change.

    Focus moved off the workspace, so if the local boot does not build and
    hand over a ViewState, the app reads focus off None forever and resume
    quietly stops restoring the focused tab — with every unit test above
    still green, because they construct AegisApp directly.
    """
    from aegis.cli import LocalTuiAttachment
    from aegis.config.roots import AegisRoots
    from aegis.views.state import load_view

    captured: dict = {}

    class _RecordingApp:
        def __init__(self, *args, **kwargs):
            captured["kwargs"] = kwargs

        async def run_async(self):
            # Whatever the app writes into its view state must be what the
            # attachment persists — same object, not a copy.
            captured["kwargs"]["view_state"].active_handle = "lucid-knuth"

    import aegis.cli
    original = aegis.cli.AegisApp
    aegis.cli.AegisApp = _RecordingApp
    try:
        roots = AegisRoots.for_project(tmp_path)
        ui = LocalTuiAttachment(clean=True, agent=None, queues={}, voice=None,
                                hosts={}, host_registry=None, drivers={},
                                cwd=str(tmp_path), agents={}, roots=roots)

        class _Mgr:
            make_session = None
            mcp = None
            roots = None

        mgr = _Mgr()
        mgr.roots = roots
        await ui.run(mgr)
    finally:
        aegis.cli.AegisApp = original

    vs = captured["kwargs"].get("view_state")
    assert vs is not None, "the local TUI boot passed no ViewState"
    persisted = load_view(roots.state_dir, vs.view_id)
    assert persisted is not None, (
        f"nothing was persisted for view {vs.view_id!r} on exit")
    assert persisted.active_handle == "lucid-knuth"


def test_focus_survives_a_kill_not_only_a_clean_exit(tmp_path):
    """Focus used to ride in workspace.json, written on every tab change.
    Persisting it only on exit would silently narrow that to clean exits."""
    from aegis.tui.app import write_view_snapshot
    from aegis.views.state import ViewState, load_view
    vs = ViewState(view_id="tty", geometry=(80, 24))
    write_view_snapshot(tmp_path, vs, "lucid-knuth")
    assert load_view(tmp_path, "tty").active_handle == "lucid-knuth"
