"""The guided smoke test's two mechanical steps (know-how: the smoke test).

    uv run python scripts/smoke.py spawn --port 8791 --root ~/aegis-smoke --prompt-file tour.md
    uv run python scripts/smoke.py read ~/aegis-smoke/.aegis/state/transcripts/<log_id>.jsonl

`spawn` creates a session in a running aegis the way the browser does: a
websocket hello with the server's token, then `session.spawn`. The tab shows
up in every window open on that server. `read` prints a transcript as the
conversation it was: the person's messages, the agent's prose and tool calls,
each `turn_end`, and the tool errors, so the problems a tour found can be
listed without scrolling the UI.

`read --asked` exits 0 once the agent has ended a turn with `turn_end`, read
from aegis's own `turn_end` record. Never grep the transcript for the word:
the prompt that asks for `turn_end` is in the transcript too.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROTO = 3


async def spawn(port: int, root: Path, agent: str, prompt: str, cwd: Path) -> dict:
    import websockets

    token = (root / ".aegis" / "state" / "token").read_text().strip()
    url = f"ws://127.0.0.1:{port}/ws"
    async with websockets.connect(url, origin=f"http://127.0.0.1:{port}") as ws:
        await ws.send(json.dumps({"t": "hello", "proto": PROTO, "token": token}))
        welcome = json.loads(await ws.recv())
        if welcome.get("t") != "welcome":
            raise SystemExit(f"not welcomed: {welcome}")
        params = {"agent": agent, "cwd": str(cwd), "prompt": prompt}
        await ws.send(
            json.dumps({"t": "call", "id": 1, "op": "session.spawn", "params": params})
        )
        while True:
            m = json.loads(await ws.recv())
            if m.get("t") == "reply" and m.get("id") == 1:
                if "error" in m:
                    raise SystemExit(f"session.spawn refused: {m['error']}")
                return m["result"]


def conversation(path: Path) -> list[str]:
    """The transcript as lines: who said what, which tools ran, how turns ended."""
    out: list[str] = []
    for raw in path.read_text().splitlines():
        rec = json.loads(raw)
        if rec.get("src") == "aegis":
            kind = rec.get("kind")
            if kind == "send":
                out.append(f"\n### PERSON: {rec['text']}")
            elif kind == "turn_end":
                out.append(f"  [turn_end {rec.get('attention')}] {rec.get('line')}")
            continue
        try:
            msg = json.loads(rec["line"])
        except (KeyError, ValueError):
            continue
        content = msg.get("message", {}).get("content")
        if msg.get("type") == "assistant":
            for c in content or []:
                if c.get("type") == "text":
                    out.append(f"AGENT: {c['text']}")
                elif c.get("type") == "tool_use":
                    out.append(f"  -> {c['name']} {json.dumps(c.get('input'))[:200]}")
        elif msg.get("type") == "user" and isinstance(content, list):
            for c in content:
                if c.get("type") == "tool_result" and c.get("is_error"):
                    body = c.get("content")
                    body = body if isinstance(body, str) else json.dumps(body)
                    out.append(f"  !! tool error: {body[:400]}")
    return out


def asked(path: Path) -> bool:
    for raw in path.read_text().splitlines():
        rec = json.loads(raw)
        if rec.get("src") == "aegis" and rec.get("kind") == "turn_end":
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spawn", help="create a session in a running aegis")
    sp.add_argument("--port", type=int, required=True)
    sp.add_argument(
        "--root",
        type=Path,
        required=True,
        help="the server's root (its .aegis/state holds the token)",
    )
    sp.add_argument("--agent", default="opus")
    sp.add_argument("--prompt-file", type=Path, required=True)
    sp.add_argument(
        "--cwd", type=Path, help="the session's directory; the root if omitted"
    )
    rp = sub.add_parser("read", help="print a transcript as a conversation")
    rp.add_argument("transcript", type=Path)
    rp.add_argument(
        "--asked",
        action="store_true",
        help="exit 0 once a turn_end is recorded, 1 before",
    )
    args = ap.parse_args()

    if args.cmd == "spawn":
        root = args.root.expanduser()
        r = asyncio.run(
            spawn(
                args.port,
                root,
                args.agent,
                args.prompt_file.read_text(),
                args.cwd or root,
            )
        )
        print(json.dumps(r))
        return 0
    if args.asked:
        return 0 if asked(args.transcript) else 1
    print("\n".join(conversation(args.transcript)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
