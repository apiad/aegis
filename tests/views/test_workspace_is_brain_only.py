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


def test_focus_survives_a_kill_not_only_a_clean_exit(tmp_path):
    """Focus used to ride in workspace.json, written on every tab change.
    Persisting it only on exit would silently narrow that to clean exits."""
    from aegis.tui.app import write_view_snapshot
    from aegis.views.state import ViewState, load_view
    vs = ViewState(view_id="tty", geometry=(80, 24))
    write_view_snapshot(tmp_path, vs, "lucid-knuth")
    assert load_view(tmp_path, "tty").active_handle == "lucid-knuth"
