"""A tiny MCP server over stdio with one tool, echo, for the skill tests.
Standard library only, since it runs on the session user's python3. It
writes its pid to pid.txt in its own folder, so a test can watch it stop."""

import json
import os
import sys

with open("pid.txt", "w") as f:
    f.write(f"{os.getpid()} {os.getcwd()}")

TOOL = {
    "name": "echo",
    "description": "Echo the text back.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


PINGED = {
    "name": "pinged",
    "description": "Ask the client first, then answer.",
    "inputSchema": {"type": "object", "properties": {}},
}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def answer(n, result):
    send({"jsonrpc": "2.0", "id": n, "result": result})


def ask(n, method):
    """A request to the client with the same id as the call it answers, so a
    client that took it for that call's answer would show; its reply."""
    send({"jsonrpc": "2.0", "id": n, "method": method})
    return json.loads(sys.stdin.readline())


while line := sys.stdin.readline():
    msg = json.loads(line)
    method, n = msg.get("method"), msg.get("id")
    if method == "initialize":
        answer(
            n,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo", "version": "1"},
            },
        )
    elif method == "tools/list":
        answer(n, {"tools": [TOOL, PINGED]})
    elif method == "tools/call" and msg["params"]["name"] == "pinged":
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {}})
        pong = ask(n, "ping")
        other = ask(n, "roots/list")
        said = f"ping {pong.get('result')!r}, roots/list {other['error']['code']}"
        answer(n, {"content": [{"type": "text", "text": said}], "isError": False})
    elif method == "tools/call":
        text = msg["params"]["arguments"].get("text", "")
        answer(
            n,
            {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False},
        )
    elif n is not None:
        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": n,
                    "error": {"code": -32601, "message": "no such method"},
                }
            )
            + "\n"
        )
        sys.stdout.flush()
