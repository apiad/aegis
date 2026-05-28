"""Registered @tool functions appear in the FastMCP server's tool list."""
from __future__ import annotations

import pytest

from aegis.tools import tool
from aegis.tools.decorator import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _fake_bridge():
    class B:
        remotes = {}
        groups = None
    return B()


@pytest.mark.asyncio
async def test_user_tool_appears_in_server(monkeypatch) -> None:
    @tool
    async def load_skill(name: str) -> str:
        """Load a skill body."""
        return f"body of {name}"

    from aegis.mcp.server import build_server
    server = build_server(_fake_bridge())
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "load_skill" in names


@pytest.mark.asyncio
async def test_user_tool_callable_through_server(monkeypatch) -> None:
    @tool
    async def echo(text: str) -> str:
        """Echo input."""
        return text + "!"

    from aegis.mcp.server import build_server
    server = build_server(_fake_bridge())
    result = await server.call_tool("echo", {"text": "hi"})
    assert "hi!" in str(result)
