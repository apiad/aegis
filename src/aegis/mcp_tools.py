"""The fastmcp half of the MCP endpoint (``mcp.py`` has the rest): each agent
operation as a tool, and the ASGI app that serves them at ``/mcp``.

Importing this module imports fastmcp, about 1.5 s. Only ``web.py`` imports it,
after the server is listening (#506).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import Tool
from fastmcp.tools import ToolResult
from fastmcp.utilities.logging import configure_logging
from pydantic import PrivateAttr

from .harness import HEADER, SHELL_HEADER
from .mcp import PATH, Tokens
from .ops import Caller, Operation, OpError, Registry

# fastmcp logs every ToolError with its traceback, and every refused op is one
# (a no_session is an answer, not a crash). Its own handler renders that
# traceback rich, which held the event loop, and every session on it, for
# about 0.5 s per refusal (#366). Plain text costs a few milliseconds. Rich
# also wraps the message itself, in time growing with its length squared: a
# page's 4,000-character error, logged twice through its chained exception,
# held the loop for seconds on a busy host (#409). So no rich handler at all.
fastmcp.settings.enable_rich_logging = False
configure_logging(level=fastmcp.settings.log_level)

if TYPE_CHECKING:
    from .registry import Registry as Sessions


class OpTool(Tool):
    _op: Operation = PrivateAttr()
    _registry: Registry = PrivateAttr()
    _tokens: Tokens = PrivateAttr()
    _sessions: Sessions | None = PrivateAttr(default=None)

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
        # A notice rides on the agent's next tool result, never on aegis
        # call's output, which a program may parse.
        reg = self._sessions
        s = None if reg is None else reg.sessions.get(log_id)
        if reg is not None and s is not None and SHELL_HEADER.lower() not in headers:
            if blocks := reg.notice_blocks(s):
                text = f"{text}\n\n{blocks}"
        return ToolResult(content=text)


def _schema(op: Operation) -> dict:
    schema = op.params.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("properties", {})
    return schema


# Where the model chooses a tool, so a loop of calls can become one Bash call.
SHELL_FORM = (
    "\n\nIn a loop, or to pipe the result into another command, run "
    "`aegis call {name} key=value ...` in Bash instead: same result, as you."
)


def build_mcp(registry: Registry, tokens: Tokens, sessions: Sessions | None = None):
    """The fastmcp server and its ASGI app, routed at ``/mcp``."""
    server = FastMCP("aegis")
    for op in registry.agent_ops():
        doc = (op.doc or op.name) + SHELL_FORM.format(name=op.tool_name)
        tool = OpTool(name=op.tool_name, description=doc, parameters=_schema(op))
        tool._op, tool._registry, tool._tokens = op, registry, tokens
        tool._sessions = sessions
        server.add_tool(tool)
    return server, server.http_app(path=PATH, stateless_http=True, json_response=True)
