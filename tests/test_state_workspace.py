# tests/test_state_workspace.py
import json
import pytest

from aegis.state.workspace import (
    CorruptWorkspace, Workspace, WorkspaceTab,
    load, save, state_dir,
)


def test_state_dir_is_project_rooted(tmp_path):
    assert state_dir(tmp_path) == tmp_path / ".aegis" / "state"


def test_load_missing_returns_none(tmp_path):
    assert load(state_dir(tmp_path)) is None


def test_save_then_load_roundtrip(tmp_path):
    sd = state_dir(tmp_path)
    ws = Workspace(
        active_handle="lucid-knuth",
        tabs=[
            WorkspaceTab(handle="lucid-knuth", profile="default",
                         order=0, provider="claude-code",
                         session_id="abc-123",
                         created_at="2026-05-21T14:00:00Z"),
            WorkspaceTab(handle="wry-hopper", profile="fast",
                         order=1, provider="gemini",
                         session_id=None,
                         created_at="2026-05-21T15:30:00Z"),
        ],
    )
    save(sd, ws)
    out = load(sd)
    assert out == ws


def test_save_creates_parent_dirs(tmp_path):
    sd = state_dir(tmp_path)
    assert not sd.exists()
    ws = Workspace(active_handle=None, tabs=[])
    save(sd, ws)
    assert (sd / "workspace.json").exists()


def test_save_is_atomic_no_partial_file_on_crash(tmp_path, monkeypatch):
    """A partway-through save must not leave a half-written workspace.json."""
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text(
        json.dumps({"version": 1, "saved_at": "old",
                    "active_handle": None, "tabs": []}))
    # Simulate a write that fails partway: monkeypatch os.replace to raise.
    import os
    orig = os.replace
    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    ws = Workspace(active_handle="x", tabs=[])
    with pytest.raises(OSError):
        save(sd, ws)
    monkeypatch.setattr(os, "replace", orig)
    # Original file untouched.
    on_disk = json.loads((sd / "workspace.json").read_text())
    assert on_disk["saved_at"] == "old"


def test_load_corrupt_raises(tmp_path):
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")
    with pytest.raises(CorruptWorkspace):
        load(sd)


def test_load_wrong_version_raises(tmp_path):
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text(
        json.dumps({"version": 99, "saved_at": "x",
                    "active_handle": None, "tabs": []}))
    with pytest.raises(CorruptWorkspace):
        load(sd)
