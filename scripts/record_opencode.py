"""Record OpenCode's event stream for the parser's fixtures.

Runs a real ``opencode serve`` (OpenCode 2) in a temporary directory with a
free model and writes one file per scenario to tests/fixtures/opencode/: the
``data:`` payloads of the scenario's session and its children, in order, one
JSON per line. Re-run it after an OpenCode upgrade and read what
tests/test_opencode_stream.py says.

    uv run python scripts/record_opencode.py [--model opencode/cat-jam-free] [--bin opencode]
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
COMMAND = {"hello": {"template": "Say hello to $ARGUMENTS in exactly three words."}}
DONE = (
    "session.execution.succeeded",
    "session.execution.failed",
    "session.execution.interrupted",
)


class Server:
    def __init__(self, bin: str, cwd: Path) -> None:
        self.cwd = cwd
        pw = secrets.token_hex(8)
        env = dict(
            os.environ,
            OPENCODE_SERVER_PASSWORD=pw,
            OPENCODE_CONFIG_CONTENT=json.dumps(
                {
                    "commands": COMMAND,
                    "permissions": [
                        {"action": "*", "resource": "*", "effect": "allow"}
                    ],
                }
            ),
        )
        self.proc = subprocess.Popen(
            [bin, "serve", "--hostname", "127.0.0.1", "--port", "0"],
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
            out = json.loads(raw) if raw else None
            return out.get("data", out) if isinstance(out, dict) else out

    def _pump(self) -> None:
        r = urllib.request.Request(
            f"{self.base}/api/event?directory={self.cwd}",
            headers={"authorization": self.auth},
        )
        for line in urllib.request.urlopen(r, timeout=3600):
            if line.startswith(b"data:"):
                self.events.append(json.loads(line[5:]))

    def done_after(self, n: int, sid: str, count: int = 1, limit: float = 300) -> None:
        end = time.time() + limit
        while time.time() < end:
            seen = sum(
                e["type"] in DONE and (e.get("data") or {}).get("sessionID") == sid
                for e in self.events[n:]
            )
            if seen >= count:
                time.sleep(1.5)  # late events after an interrupt
                return
            time.sleep(0.2)
        raise SystemExit(f"no end of turn for {sid} in {limit}s")

    def save(self, name: str, n: int, sid: str) -> None:
        mine, out = {sid}, []
        for e in self.events[n:]:
            d = e.get("data") or {}
            if e["type"] == "session.created" and d.get("parentID") in mine:
                mine.add(d["sessionID"])
            owner = d.get("sessionID") or (
                (d.get("info") or {}).get("metadata") or {}
            ).get("sessionID")
            if owner in mine:
                out.append(json.dumps(e))
        (OUT / f"{name}.jsonl").write_text("\n".join(out) + "\n")
        print(f"{name}: {len(out)} events")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opencode/cat-jam-free")
    ap.add_argument("--bin", default="opencode")
    args = ap.parse_args()
    provider, _, model_id = args.model.partition("/")
    M = {"providerID": provider, "id": model_id}
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        (cwd / "notes.txt").write_text("alpha\nbeta\n")
        srv = Server(args.bin, cwd)
        try:

            def turn(
                name: str, *texts: str, gap: float = 0, interrupt_after: float = 0
            ):
                n = len(srv.events)
                sid = srv.req("POST", "/api/session", {"model": M})["id"]
                for k, text in enumerate(texts):
                    if k:
                        time.sleep(gap)
                    srv.req("POST", f"/api/session/{sid}/prompt", {"text": text})
                if interrupt_after:
                    time.sleep(interrupt_after)
                    srv.req("POST", f"/api/session/{sid}/interrupt", {})
                srv.done_after(n, sid)
                srv.save(name, n, sid)

            turn("plain", "Reply with the single word OK.")
            turn("tool", "Run `echo hi` in the shell, then say done in one word.")
            turn("midturn", "Run `sleep 6` in the shell, then say A.",
                 "Also say B.", gap=3)  # fmt: skip
            turn("abort", "Run `sleep 30` in the shell, then say finished.",
                 interrupt_after=8)  # fmt: skip
            turn(
                "edit",
                "In notes.txt replace the word beta with gamma using your edit tool, then say done.",
            )
            turn("task", "Use a subagent to count the files in this directory, "
                 "then report the count in one line.")  # fmt: skip
            n = len(srv.events)
            sid = srv.req("POST", "/api/session", {"model": M})["id"]
            srv.req("POST", f"/api/session/{sid}/command",
                    {"name": "hello", "text": "the okapi"}, timeout=300)  # fmt: skip
            srv.done_after(n, sid)
            srv.save("command", n, sid)
        finally:
            srv.proc.terminate()


if __name__ == "__main__":
    main()
