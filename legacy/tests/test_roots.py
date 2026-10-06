from pathlib import Path

from aegis.config.roots import AegisRoots


def test_for_project_derives_all_three_from_one_path(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert roots.config_root == tmp_path
    assert roots.state_root == tmp_path
    assert roots.harness_cwd == tmp_path


def test_harness_cwd_can_differ_from_config_root(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    roots = AegisRoots.for_project(tmp_path, harness_cwd=worktree)
    assert roots.config_root == tmp_path
    assert roots.harness_cwd == worktree


def test_state_dir_is_under_state_root(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert roots.state_dir == tmp_path / ".aegis" / "state"


def test_roots_are_absolute_even_when_given_a_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    roots = AegisRoots.for_project(Path("."))
    assert roots.config_root.is_absolute()


def test_roots_are_frozen(tmp_path):
    import dataclasses
    import pytest
    roots = AegisRoots.for_project(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        roots.config_root = tmp_path / "other"


def test_state_dir_agrees_with_workspace_helper(tmp_path):
    from aegis.state.workspace import state_dir as workspace_state_dir
    roots = AegisRoots.for_project(tmp_path)
    assert roots.state_dir == workspace_state_dir(tmp_path)
