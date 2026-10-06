"""The `--remote` mode left twenty branches in the TUI; none may survive it.

A dead `hasattr(self, "_remote_manager")` reads as a live mode to the next
person, and each one guards a real behaviour (spawn, quit, persistence, the
fleet dashboard) whose remote half no longer exists.
"""

import ast
import inspect
from pathlib import Path

import aegis.tui.app
import aegis.tui.pane

SOURCES = [Path(inspect.getfile(m)) for m in (aegis.tui.app, aegis.tui.pane)]


def test_no_module_named_remote_manager_or_ws_client():
    import importlib.util

    for name in ("aegis.tui.remote_manager", "aegis.tui.ws_client"):
        assert importlib.util.find_spec(name) is None, f"{name} still exists"


def test_no_remote_manager_branch_survives():
    for path in SOURCES:
        text = path.read_text()
        assert "_remote_manager" not in text, f"{path.name} still branches on it"
        assert "_DisabledPlaneStub" not in text, f"{path.name} still stubs planes"


def test_the_app_no_longer_takes_a_manager():
    sig = inspect.signature(aegis.tui.app.AegisApp.__init__)
    assert "manager" not in sig.parameters, (
        "`manager=` was the --remote seam; `bridge=` is the embedded one"
    )


def test_the_tui_module_still_parses_and_carries_its_bindings():
    """A de-branching that broke an `if` would still import; this asserts the
    class survived with its key map, which is what a user touches."""
    for path in SOURCES:
        ast.parse(path.read_text())
    keys = {b.key for b in aegis.tui.app.AegisApp.BINDINGS}
    assert {"ctrl+t", "ctrl+q"} <= keys, keys
