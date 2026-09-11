from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager


def _mgr(root: Path) -> SessionManager:
    return SessionManager(
        agents={}, default_agent="", make_session=lambda *a, **k: None,
        mcp=None, roots=AegisRoots.for_project(root))


def test_state_root_is_set_without_a_scheduler(tmp_path):
    """Regression: state_root was only ever assigned by
    attach_scheduler_context, so every non-scheduler boot fell back to cwd."""
    mgr = _mgr(tmp_path)
    assert mgr.state_root == tmp_path


def test_state_root_ignores_the_process_cwd(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    mgr = _mgr(project)
    monkeypatch.chdir(elsewhere)
    assert mgr.state_root == project


def test_two_managers_have_disjoint_state_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    assert _mgr(a).state_root != _mgr(b).state_root
