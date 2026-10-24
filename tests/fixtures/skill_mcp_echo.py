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


def answer(n, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": n, "result": result}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
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
        answer(n, {"tools": [TOOL]})
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
