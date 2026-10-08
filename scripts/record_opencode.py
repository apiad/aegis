"""Record OpenCode's event stream for the parser's fixtures.

Runs a real ``opencode serve`` in a temporary directory (a few cents of the
model below) and writes one file per scenario to tests/fixtures/opencode/: the
``data:`` payloads of the scenario's session and its children, in order, the
types aegis stores plus deltas, one JSON per line. Re-run it after an OpenCode
upgrade and read what tests/test_opencode_stream.py says.

    uv run python scripts/record_opencode.py [--model opencode-go/deepseek-v4-flash]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "opencode"
KEEP = {
    "session.created",
    "session.updated",
    "session.status",
    "session.idle",
    "session.error",
    "session.compacted",
    "message.updated",
    "message.part.updated",
    "message.part.delta",
}
COMMAND = {"hello": {"template": "Say hello to $ARGUMENTS in exactly three words."}}


class Server:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        pw = secrets.token_hex(8)
        env = dict(
            os.environ,
            OPENCODE_SERVER_PASSWORD=pw,
            OPENCODE_CONFIG_CONTENT=json.dumps(
                {"command": COMMAND, "permission": {"*": "allow", "question": "deny"}}
            ),
        )
        self.proc = subprocess.Popen(
            ["opencode", "serve", "--hostname", "127.0.0.1", "--port", "0"],
            cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise SystemExit("opencode serve exited before listening")
            if m := re.search(r"listening on (http://\S+)", line):
                self.base = m.group(1)
                break
        self.auth = "Basic " + base64.b64encode(f"opencode:{pw}".encode()).decode()
        self.events: list[dict] = []
        threading.Thread(target=self._pump, daemon=True).start()
        time.sleep(1)

    def req(
        self, method: str, path: str, body: dict | None = None, timeout: float = 30
    ):
        r = urllib.request.Request(
            f"{self.base}{path}?directory={self.cwd}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"content-type": "application/json", "authorization": self.auth},
        )  # fmt: skip
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def _pump(self) -> None:
        r = urllib.request.Request(
            f"{self.base}/event?directory={self.cwd}",
            headers={"authorization": self.auth},
        )
        for line in urllib.request.urlopen(r, timeout=3600):
            if line.startswith(b"data:"):
                self.events.append(json.loads(line[5:]))

    def idle_after(self, n: int, sid: str, limit: float = 300) -> None:
        end = time.time() + limit
        while time.time() < end:
            if any(
                e["type"] == "session.idle" and e["properties"].get("sessionID") == sid
                for e in self.events[n:]
            ):
                time.sleep(1.5)  # late parts after an abort's idle
                return
            time.sleep(0.2)
        raise SystemExit(f"no idle for {sid} in {limit}s")

    def save(self, name: str, n: int, sid: str) -> None:
        mine, out = {sid}, []
        for e in self.events[n:]:
            p = e.get("properties") or {}
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            if e["type"] == "session.created" and info.get("parentID") in mine:
                mine.add(info["id"])
            owner = (
                p.get("sessionID")
                or info.get("id")
                or (p.get("part") or {}).get("sessionID")
            )
            if e["type"] in KEEP and owner in mine:
                out.append(json.dumps(e))
        (OUT / f"{name}.jsonl").write_text("\n".join(out) + "\n")
        print(f"{name}: {len(out)} events")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opencode-go/deepseek-v4-flash")
    model = ap.parse_args().model
    provider, _, model_id = model.partition("/")
    M = {"providerID": provider, "modelID": model_id}
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        (cwd / "notes.txt").write_text("alpha\nbeta\n")
        srv = Server(cwd)
        try:

            def turn(name: str, *texts: str, gap: float = 0, abort_after: float = 0):
                n = len(srv.events)
                sid = srv.req("POST", "/session", {})["id"]
                for k, text in enumerate(texts):
                    if k:
                        time.sleep(gap)
                    srv.req("POST", f"/session/{sid}/prompt_async",
                            {"model": M, "parts": [{"type": "text", "text": text}]})  # fmt: skip
                if abort_after:
                    time.sleep(abort_after)
                    srv.req("POST", f"/session/{sid}/abort", {})
                srv.idle_after(n, sid)
                srv.save(name, n, sid)

            turn("plain", "Reply with the single word OK.")
            turn("tool", "Run `echo hi` in bash, then say done in one word.")
            turn("midturn", "Run `sleep 6` in bash, then say A.",
                 "Also say B.", gap=3)  # fmt: skip
            turn("abort", "Run `sleep 30` in bash, then say finished.", abort_after=8)
            turn(
                "edit",
                "In notes.txt replace the word beta with gamma using the edit tool, then say done.",
            )
            turn("task", "Use the task tool with the general subagent to count the files "
                 "in this directory, then report the count in one line.")  # fmt: skip
            n = len(srv.events)
            sid = srv.req("POST", "/session", {})["id"]
            srv.req("POST", f"/session/{sid}/command",
                    {"command": "hello", "arguments": "the okapi", "model": model}, timeout=300)  # fmt: skip
            srv.idle_after(n, sid)
            srv.save("command", n, sid)
        finally:
            srv.proc.terminate()


if __name__ == "__main__":
    main()
