"""``aegis call`` and ``aegis tools``: the agent's MCP tools from a shell.

A thin client of the same ``/mcp`` endpoint, with the same token: aegis
exports ``AEGIS_MCP_URL`` and ``AEGIS_SESSION_TOKEN`` to every session it
spawns (``harness.session_env``), so a call from the agent's shell runs as that
session with the same operation, permissions and result as the tool. Outside a
session there is no token and every call is refused.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
import typer

from .harness import HEADER, SHELL_HEADER, TOKEN_VAR, URL_VAR


def _post(method: str, params: dict) -> dict:
    url, token = os.environ.get(URL_VAR), os.environ.get(TOKEN_VAR)
    if not url or not token:
        typer.echo(
            f"not inside an aegis session: {URL_VAR} or {TOKEN_VAR} unset", err=True
        )
        raise typer.Exit(2)
    try:
        r = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={
                HEADER: token,
                SHELL_HEADER: "1",
                "Accept": "application/json, text/event-stream",
            },
            timeout=60,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        typer.echo(f"aegis at {url} did not answer: {e}", err=True)
        raise typer.Exit(1) from e
    body = r.json()
    if "error" in body:
        typer.echo(body["error"].get("message", body["error"]), err=True)
        raise typer.Exit(1)
    return body["result"]


def _value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def call(tool: str, args: list[str]) -> None:
    if args == ["-"]:
        arguments = json.load(sys.stdin)
    elif len(args) == 1 and args[0].lstrip().startswith("{"):
        arguments = json.loads(args[0])
    else:
        arguments = {}
        for a in args:
            key, sep, val = a.partition("=")
            if not sep:
                raise typer.BadParameter(f"expected key=value, got {a!r}")
            arguments[key] = _value(val)
    result = _post("tools/call", {"name": tool, "arguments": arguments})
    text = "\n".join(c.get("text", "") for c in result.get("content", []))
    if result.get("isError"):
        typer.echo(text, err=True)
        raise typer.Exit(1)
    typer.echo(text)


def tools(name: str | None) -> None:
    listed = _post("tools/list", {})["tools"]
    if name is None:
        for t in listed:
            typer.echo(f"{t['name']}\t{(t.get('description') or '').splitlines()[0]}")
        return
    for t in listed:
        if t["name"] == name:
            typer.echo(t.get("description") or "")
            typer.echo(json.dumps(t["inputSchema"], indent=1))
            return
    typer.echo(f"no tool {name}", err=True)
    raise typer.Exit(1)
