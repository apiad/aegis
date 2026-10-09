"""Record codex app-server's lines for the parser's fixtures.

Runs a real ``codex app-server`` in a temporary directory with a throwaway
CODEX_HOME, on a free OpenRouter model, so it costs nothing and needs only
OPENROUTER_API_KEY. Writes one file per scenario to tests/fixtures/codex/: the
lines CodexProcess would hand the session, in order (its own ``aegis/*`` lines
and the notifications in ``codex.stream.STORED`` and ``DELTAS``). Re-run it
after a Codex upgrade and read what tests/test_codex_stream.py says. Free
models are rate-limited upstream; a scenario that fails is reported and the
others still run.

    uv run python scripts/record_codex.py [--model nvidia/nemotron-3-super-120b-a12b:free] [--only NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from aegis.codex.stream import DELTAS, STORED

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "codex"
PROVIDER = [
    "-c", 'model_providers.openrouter.name="OpenRouter"',
    "-c", 'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"',
    "-c", 'model_providers.openrouter.env_key="OPENROUTER_API_KEY"',
    "-c", 'model_providers.openrouter.wire_api="responses"',
]  # fmt: skip
TOKEN = "tok-record"


class AppServer:
    def __init__(self, home: Path, cwd: Path, mcp_url: str | None) -> None:
        args = ["codex", "app-server", "--disable", "plugins", "--disable",
                "remote_plugin", *PROVIDER, "-c", 'approval_policy="never"']  # fmt: skip
        if mcp_url:
            args += [
                "-c", f"mcp_servers.aegis.url={json.dumps(mcp_url)}",
                "-c", 'mcp_servers.aegis.env_http_headers={"X-Aegis-Session"="AEGIS_SESSION_TOKEN"}',
                "-c", 'mcp_servers.aegis.default_tools_approval_mode="approve"',
            ]  # fmt: skip
        env = {**os.environ, "CODEX_HOME": str(home), "AEGIS_SESSION_TOKEN": TOKEN}
        self.p = subprocess.Popen(
            args, cwd=cwd, env=env, text=True, start_new_session=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        self.lines: list[str] = []
        self.events: queue.Queue[dict] = queue.Queue()
        self.replies: dict[int, dict] = {}
        self.cv = threading.Condition()
        self.n = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.p.stdout is not None
        for raw in self.p.stdout:
            msg = json.loads(raw)
            if "id" in msg and "method" in msg:
                self.send({"id": msg["id"], "error": {"code": -32601, "message": "no"}})
            elif "id" in msg:
                with self.cv:
                    self.replies[msg["id"]] = msg
                    self.cv.notify_all()
            else:
                if msg.get("method") in STORED or msg.get("method") in DELTAS:
                    self.lines.append(raw.strip())
                self.events.put(msg)

    def send(self, obj: dict) -> None:
        assert self.p.stdin is not None
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
        self.p.stdin.flush()

    def call(self, method: str, params: dict, timeout: float = 60) -> dict:
        self.n += 1
        rid = self.n
        self.send({"id": rid, "method": method, "params": params})
        with self.cv:
            if not self.cv.wait_for(lambda: rid in self.replies, timeout):
                raise TimeoutError(method)
        r = self.replies.pop(rid)
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r["result"]

    def own(self, method: str, params: dict) -> None:
        self.lines.append(json.dumps({"method": method, "params": params}))

    def wait(self, pred, timeout: float = 300) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self.events.get(timeout=1)
            except queue.Empty:
                continue
            if pred(msg):
                return msg
        raise TimeoutError("event not seen")

    def close(self) -> None:
        assert self.p.stdin is not None
        self.p.stdin.close()
        try:
            self.p.wait(30)
        finally:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def open_thread(s: AppServer, model: str, cwd: Path) -> str:
    s.own("aegis/initialize", s.call("initialize", {"clientInfo": {"name": "aegis", "version": "record"}}))
    s.send({"method": "initialized"})
    th = s.call("thread/start", {"cwd": str(cwd), "model": model, "modelProvider": "openrouter",
                                 "sandbox": "danger-full-access", "approvalPolicy": "never"})  # fmt: skip
    s.own("aegis/thread", th)
    return th["thread"]["id"]


def start_turn(s: AppServer, tid: str, model: str, text: str) -> str:
    s.own("aegis/turn", {"model": f"openrouter/{model}", "effort": "", "permission": "full"})
    t = s.call("turn/start", {"threadId": tid, "model": model,
                              "input": [{"type": "text", "text": text, "text_elements": []}],
                              "sandboxPolicy": {"type": "dangerFullAccess"}})  # fmt: skip
    return t["turn"]["id"]


def ended(s: AppServer, tid: str) -> dict:
    return s.wait(lambda m: m.get("method") == "turn/completed" and m["params"]["threadId"] == tid)


def command_started(s: AppServer) -> dict:
    return s.wait(lambda m: m.get("method") == "item/started"
                  and m["params"]["item"]["type"] == "commandExecution")  # fmt: skip


def run(name: str, s: AppServer, model: str, cwd: Path) -> None:
    tid = open_thread(s, model, cwd)
    if name == "plain":
        start_turn(s, tid, model, "Reply with the single word OK.")
        ended(s, tid)
    elif name == "tool":
        start_turn(s, tid, model, "Run the shell command `echo hi` and reply with its output.")
        ended(s, tid)
    elif name == "mcp":
        start_turn(s, tid, model, "Call the aegis whoami tool and repeat its answer.")
        ended(s, tid)
    elif name == "steer":
        turn = start_turn(s, tid, model, "Run the shell command `sleep 8`, then say finished.")
        command_started(s)
        s.call("turn/steer", {"threadId": tid, "expectedTurnId": turn,
                              "input": [{"type": "text", "text": "Also say the word MANGO.", "text_elements": []}]})  # fmt: skip
        ended(s, tid)
    elif name == "interrupt":
        turn = start_turn(s, tid, model, "Run the shell command `sleep 60`, then say finished.")
        command_started(s)
        s.call("turn/interrupt", {"threadId": tid, "turnId": turn})
        ended(s, tid)
    elif name == "subagent":
        start_turn(s, tid, model, "Use your spawn_agent tool to start one sub-agent whose task is "
                   "to reply with the word PONG. Wait for it, then report what it said.")  # fmt: skip
        ended(s, tid)
    elif name == "compact":
        start_turn(s, tid, model, "Reply with the single word OK.")
        ended(s, tid)
        s.own("aegis/command", {"line": "/compact", "kind": "compact"})
        s.call("thread/compact/start", {"threadId": tid})
        ended(s, tid)
    elif name == "patch":
        start_turn(s, tid, model, "Use the apply_patch tool, not the shell, to change the word "
                   "hello to bye in a.txt.")  # fmt: skip
        ended(s, tid)


def mcp_server(port: int) -> None:
    from fastmcp import FastMCP
    from fastmcp.server.dependencies import get_http_headers

    mcp = FastMCP("aegis")

    @mcp.tool
    def whoami() -> str:
        """Say which aegis session is calling."""
        return "you are session " + get_http_headers().get("x-aegis-session", "<none>")

    threading.Thread(
        target=lambda: mcp.run(transport="http", host="127.0.0.1", port=port, path="/mcp"),
        daemon=True,
    ).start()
    for _ in range(100):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError("the MCP stand-in did not listen")


SCENARIOS = ("plain", "tool", "mcp", "steer", "interrupt", "subagent", "compact", "patch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="nvidia/nemotron-3-super-120b-a12b:free")
    ap.add_argument("--only")
    a = ap.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    mcp_server(port)
    OUT.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in SCENARIOS:
        if a.only and name != a.only:
            continue
        with tempfile.TemporaryDirectory() as d:
            home, cwd = Path(d) / "home", Path(d) / "work"
            home.mkdir()
            cwd.mkdir()
            (cwd / "a.txt").write_text("hello\n")
            s = AppServer(home, cwd, f"http://127.0.0.1:{port}/mcp")
            try:
                run(name, s, a.model, cwd)
            except (TimeoutError, RuntimeError) as e:
                print(f"{name}: FAILED {e}")
                failed += 1
                continue
            finally:
                s.close()
            (OUT / f"{name}.jsonl").write_text("\n".join(s.lines) + "\n")
            print(f"{name}: {len(s.lines)} lines")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
