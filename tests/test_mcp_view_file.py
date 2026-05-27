from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.mcp.server import build_server


async def _call(server, name, **kwargs):
    """Mirror the helper in test_mcp_server.py."""
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


class _FakeBridge:
    canvas_manager = MagicMock()
    terminal_manager = MagicMock()
    groups = MagicMock()
    remotes: dict = {}
    scheduler = None
    workflow_registry = MagicMock()
    queue_manager = None

    def __init__(self, state_root: Path, *, has_open_file: bool = True) -> None:
        from aegis.queue import InboxRouter
        self.inbox_router = InboxRouter()
        self.state_root = state_root
        self.workflow_registry.get.return_value = None
        if has_open_file:
            self.open_file = AsyncMock(
                return_value={"status": "opened",
                              "path": str(state_root / "x.py")})

    def list_sessions(self): return []
    def list_agents(self): return []
    def inline_schedule_names(self): return set()
    async def handoff(self, a, b, c): return "ok"
    async def spawn(self, profile, *, handle=None): return "h"
    async def close(self, handle): pass


@pytest.mark.asyncio
async def test_aegis_view_file_calls_open_file(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("hello")
    bridge = _FakeBridge(tmp_path)
    server = build_server(bridge)
    result = await _call(server, "aegis_view_file", path=str(f))
    bridge.open_file.assert_called_once_with(str(f))
    assert result["status"] == "opened"


@pytest.mark.asyncio
async def test_aegis_view_file_no_tui(tmp_path: Path):
    bridge = _FakeBridge(tmp_path, has_open_file=False)
    server = build_server(bridge)
    result = await _call(server, "aegis_view_file",
                         path=str(tmp_path / "x.py"))
    assert result["status"] == "no_tui"
