"""The gate for Task 4: config writes must land in the instance that made
them. Asserts on file contents, not on resolved paths — a path assertion
passes while the 26 late-bound lookups still read the process cwd."""
from pathlib import Path

CONFIG = """\
agents:
  opus:
    provider: claude-code
    model: opus
default_agent: opus
"""


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".aegis.yaml").write_text(CONFIG, encoding="utf-8")
    return root


async def test_config_write_lands_only_in_its_own_instance(tmp_path, monkeypatch):
    """Reuses the existing harness in tests/test_mcp_config_tools.py rather
    than a new one: `_StubBridge` already implements register_agent, and
    `_call` already unwraps FastMCP's ToolResult."""
    from aegis.config.roots import AegisRoots
    from aegis.mcp.server import build_server

    from tests.test_mcp_config_tools import _StubBridge, _call

    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    before_b = (b / ".aegis.yaml").read_text(encoding="utf-8")

    bridge = _StubBridge()
    bridge.roots = AegisRoots.for_project(a)   # what Task 2 threads in
    server = build_server(bridge)

    # Stand in the *other* project. A cwd-resolving implementation writes
    # to b; a roots-resolving one writes to a.
    monkeypatch.chdir(b)
    result = await _call(server, "aegis_config_add_agent",
                         slug="sonnet", harness="claude-code",
                         model="sonnet")

    assert (b / ".aegis.yaml").read_text(encoding="utf-8") == before_b, (
        "instance A's config write leaked into instance B")
    assert "sonnet" in (a / ".aegis.yaml").read_text(encoding="utf-8")
    assert result == {"ok": True, "live": True, "restart_required_for": []}
    assert bridge.registered_agents and bridge.registered_agents[0][0] == "sonnet", (
        "the hot-register (bridge.register_agent) was dropped; the tool's "
        "docstring promises it and the next spawn depends on it")


def test_no_find_project_root_calls_remain_in_mcp_server():
    """Structural guard, by AST rather than substring — a substring check is
    fooled by a line break or a mention in a comment. The fix is to stop
    calling it in this module at all, so assert on calls, not on text."""
    import ast

    import aegis.mcp.server as srv

    tree = ast.parse(Path(srv.__file__).read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "find_project_root"
    ]
    assert not calls, (
        f"mcp/server.py must resolve roots from bridge.roots; "
        f"find_project_root still called at lines {calls}")


def test_every_real_bridge_exposes_roots_to_build_server(tmp_path, monkeypatch):
    """``build_server`` now reads ``bridge.roots``, so every implementation
    of ``AppBridge`` owes it one.

    The two tests above cannot catch a missing one: both drive a hand-built
    ``_StubBridge`` whose ``.roots`` the test itself assigns. They stay green
    while the two bridges nobody stubs — ``AegisApp`` (every TUI session) and
    ``_NullBridge`` (the unbound fallback, whose docstring promises tools
    "return empty/unavailable rather than crashing") — raise AttributeError
    the moment ``AegisMCP.start()`` builds the server. This test builds the
    real objects instead.
    """
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots
    from aegis.mcp.runtime import _NullBridge
    from aegis.mcp.server import build_server
    from aegis.tui.app import AegisApp

    monkeypatch.chdir(tmp_path)

    null = _NullBridge()
    assert isinstance(null.roots, AegisRoots)
    build_server(null)

    class _FakeMCP:
        url = "http://127.0.0.1:0/mcp/"
        tokens = None

        def bind(self, bridge) -> None:
            self.bound = bridge

    app = AegisApp({"default": Agent(harness="claude-code", model="opus")},
                   "default", None, _FakeMCP(), cwd=str(tmp_path))
    assert isinstance(app.roots, AegisRoots)
    assert app.roots.config_root == Path(tmp_path).resolve()
    build_server(app)
