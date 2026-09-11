from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager


def test_project_root_ignores_process_cwd(tmp_path, monkeypatch):
    """session.py:85,91 used `project_root or Path.cwd()`, so a session
    built without an explicit root silently adopted the process cwd —
    wrong the moment two instances share a process."""
    from aegis.core.session import AgentSession
    import inspect
    sig = inspect.signature(AgentSession.__init__)
    param = sig.parameters["project_root"]
    assert param.default is inspect.Parameter.empty, (
        "project_root must be required; a default reintroduces the cwd "
        "fallback this task removes")


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _split_roots(tmp_path: Path) -> AegisRoots:
    """Roots whose state root and harness cwd genuinely differ.

    They coincide under ``for_project(root)``, which is the CLI case and the
    reason this class of bug stays invisible until aegis is embedded.
    """
    state = tmp_path / "state"
    worktree = tmp_path / "worktree"
    state.mkdir()
    worktree.mkdir()
    return AegisRoots.for_project(state, harness_cwd=worktree)


def _mgr(roots: AegisRoots) -> SessionManager:
    return SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle: _FakeHarness(),
        mcp=None, roots=roots)


def test_session_state_dir_follows_the_state_root_not_the_harness_cwd(tmp_path):
    """Per-session state belongs under ``roots.state_dir``.

    ``AgentSession.state_dir`` used to derive from ``project_root``, which
    ``SessionManager.spawn`` sets to ``roots.harness_cwd``. With an explicit
    harness_cwd the two diverge, so hooks, the digest, the recap and the plan
    wrote under the worktree while every other subsystem wrote under the
    state root — the split ``AegisRoots`` exists to prevent, one layer below
    where Task 1's guard checks for it.
    """
    roots = _split_roots(tmp_path)
    sess = _mgr(roots)._sync_spawn("default")

    assert sess.state_dir == roots.state_dir
    assert sess.state_dir.is_relative_to(roots.state_root)
    assert not sess.state_dir.is_relative_to(roots.harness_cwd), (
        "session state landed under the harness cwd; it must follow the "
        "state root")


def test_session_project_root_is_still_the_harness_cwd(tmp_path):
    """The fix must not move the harness. ``project_root`` is where the
    subprocess runs and backs ``Place``; only ``state_dir`` was wrong."""
    roots = _split_roots(tmp_path)
    sess = _mgr(roots)._sync_spawn("default")

    assert sess.project_root == roots.harness_cwd
    assert sess.place.cwd == str(roots.harness_cwd)
