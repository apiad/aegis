"""The MCP endpoint: the registry's agent operations, projected as tools.

A tool is named after its operation with the dot as an underscore
(``monitor.start`` is ``monitor_start``; Claude Code shows it as
``mcp__aegis__monitor_start``), and takes the operation's params. There is no
``from_handle``: each spawn of ``claude`` gets a token, carried in the
``X-Aegis-Session`` header of its ``--mcp-config``, and the endpoint resolves
the caller from it. A call with no valid token is refused.

The app is fastmcp's streamable HTTP app in stateless JSON mode, so every call
is one POST with no session to keep, and a server restart loses nothing.
"""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import Tool
from fastmcp.tools import ToolResult
from pydantic import PrivateAttr

from .ops import Caller, Operation, OpError, Registry

if TYPE_CHECKING:
    from .session import Session

HEADER = "X-Aegis-Session"
PATH = "/mcp"


class Tokens:
    """One token per running ``claude`` process; a restart of the process
    mints a new one and the old one stops working."""

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._by_session: dict[str, str] = {}

    def mint(self, log_id: str) -> str:
        self.drop(log_id)
        token = secrets.token_urlsafe(24)
        self._by_token[token] = log_id
        self._by_session[log_id] = token
        return token

    def drop(self, log_id: str) -> None:
        old = self._by_session.pop(log_id, None)
        if old is not None:
            self._by_token.pop(old, None)

    def resolve(self, token: str | None) -> str | None:
        return self._by_token.get(token or "")


def mcp_config(url: str, token: str) -> str:
    return json.dumps(
        {
            "mcpServers": {
                "aegis": {"type": "http", "url": url, "headers": {HEADER: token}}
            }
        }
    )


class OpTool(Tool):
    _op: Operation = PrivateAttr()
    _registry: Registry = PrivateAttr()
    _tokens: Tokens = PrivateAttr()

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        headers = get_http_headers(include_all=True) or {}
        log_id = self._tokens.resolve(headers.get(HEADER.lower()))
        if log_id is None:
            raise ToolError(
                "unknown session: this call carries no valid aegis session token"
            )
        try:
            result = await self._registry.call(
                self._op.name, arguments, Caller("agent", log_id)
            )
        except OpError as e:
            raise ToolError(f"{e.code}: {e.message}") from e
        text = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        return ToolResult(content=text)


def _schema(op: Operation) -> dict:
    schema = op.params.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("properties", {})
    return schema


def build_mcp(registry: Registry, tokens: Tokens):
    """The fastmcp server and its ASGI app, routed at ``/mcp``."""
    server = FastMCP("aegis")
    for op in registry.agent_ops():
        tool = OpTool(
            name=op.tool_name, description=op.doc or op.name, parameters=_schema(op)
        )
        tool._op, tool._registry, tool._tokens = op, registry, tokens
        server.add_tool(tool)
    return server, server.http_app(path=PATH, stateless_http=True, json_response=True)


def primer(session: Session, server_name: str) -> str:
    return PRIMER.format(handle=session.handle, server=server_name)


PRIMER = """\
You are running inside aegis, a workplace for coding agents. Your handle is \
{handle}, on the server {server}. aegis's tools are the `aegis` MCP server's \
(mcp__aegis__*); they know who you are, so no tool takes your handle.

Messages from others reach you as user turns that start with a header line: \
`> from monitor:<id> · …` when a monitor you armed ends, `> from queue:<name> · \
task#<id> · ok|error · …` when a task you enqueued finishes, `> from agent:<handle> \
· …` when another agent hands you something. Treat the body as an instruction.

To wait on a long process (tests, a build, a download), never sleep or poll: call \
monitor_start with a bash `done` condition (and `progress`, echoing 0 to 100), \
then end your turn. You are woken when it finishes, fails or times out.

If you are a queue worker, your task is the first prompt you got, and your final \
message is its result: make it the answer the enqueuer needs.\
"""
