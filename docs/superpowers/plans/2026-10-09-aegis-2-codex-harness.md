# Codex harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent whose harness is `codex` spawns, streams, steers, interrupts, resumes, calls aegis tools and reports context, cost and ChatGPT quota, like a Claude or OpenCode session.

**Architecture:** A third module behind the existing `harness.py` interface: `src/aegis/codex/` drives one `codex app-server` child per session over JSON-RPC on stdio. A stateful parser maps its notifications onto the events `claude/stream.py` already defines, so the fold, the card and the wire change nowhere. Deltas are folded live and never stored, as for OpenCode.

**Tech Stack:** Python 3.13, asyncio subprocesses, httpx (already a dependency), pytest with the repo's async fixtures, `codex-cli` 0.162.1.

**Spec:** `docs/superpowers/specs/2026-10-09-aegis-2-codex-harness-design.md` (issue #105). Read it first; this plan argues from it and from the probes in the Workspace at `.playground/codex-free/`.

## Global Constraints

- Measured version: `codex-cli 0.162.1`. Record fixtures with that version or say which one in the commit.
- The child's argv always carries `--disable plugins --disable remote_plugin` and `-c approval_policy="never"`.
- The session token reaches the child only as the environment variable `AEGIS_SESSION_TOKEN`, named by `mcp_servers.aegis.env_http_headers`, and is excluded from the model's shell with `shell_environment_policy.exclude=["AEGIS_SESSION_TOKEN"]`. Never put the token on argv.
- The aegis MCP server always has `default_tools_approval_mode="approve"`.
- A Codex model in aegis is `provider/model`: the first segment is the Codex provider id, the rest the model id, slashes included.
- Permission to sandbox: `read` → `readOnly`, `write` → `workspaceWrite` without network, `auto` → `workspaceWrite` with network, `full` → `dangerFullAccess`. Nothing ever asks.
- The child runs with `start_new_session=True`; stopping it closes stdin, waits 5 s, then signals the whole process group.
- The store src tag is `codex`; the harness name is `codex`; the label is `Codex`; `tool_prefix` is `mcp__aegis__`.
- Relative imports only, nothing from `legacy/`, no `Path.cwd()` below the CLI (`tests/test_imports.py`, `tests/test_no_cwd.py`).
- Work in the worktree `.claude/worktrees/codex-harness-design` on branch `codex-harness-design`. Commit named paths only (`git commit -- <paths>`); never `git add -A`.
- Each task runs only its own test files (`uv run pytest tests/<file> -q`). The full `make check` runs once at the end (Task 9).

## Review Focus

- **A huge line.** An `item/completed` for a command carries its whole output on one stdout line; asyncio's default `readline` limit is 64 KB and raises `ValueError` past it. A `cat` of a large file must not kill the session. Pinned in Task 4 (`test_a_line_over_64_kb_reaches_the_store`).
- **A crash that leaves the writer lease held.** If the app-server dies on its own (exit, OOM), its group may keep a child that holds the thread, and the next resume fails with "already has an active writer". `_wait` kills the group after any exit. Pinned in Task 4 (`test_an_exit_kills_what_the_child_left_and_the_resume_works`).
- **A prompt sent in the gap between turns.** `turn/steer` against a turn that just ended is refused; the send must fall back to `turn/start` instead of failing. Pinned in Task 5 (`test_a_steer_that_meets_an_ended_turn_starts_a_new_one`).
- **A request from the agent nobody answers.** Codex may send an approval or user-input request even under `never`; an unanswered request hangs the turn. Every server request gets an error reply at once. Pinned in Task 4 (`test_a_request_from_codex_is_refused_and_the_turn_goes_on`).
- **A user config that already sets an aegis MCP server or approval policy.** The `-c` flags override `~/.codex/config.toml`; a test checks the argv order so aegis's values come last. Pinned in Task 3 (`test_argv_overrides_come_after_the_subcommand`).

---

## File map

| File | Responsibility |
|---|---|
| `src/aegis/codex/__init__.py` | package marker |
| `src/aegis/codex/stream.py` | `LABEL`, `STORED`, `DELTAS`, the stateful `Parser`, `diff_pair` |
| `src/aegis/codex/config.py` | argv, environment, sandbox tables, `split_model`, `catalog_from`, `provider_model`; no I/O |
| `src/aegis/codex/process.py` | `CodexProcess` (JSON-RPC client, lifecycle, send, steer, commands, interrupt, set, catalog) and `probe` |
| `src/aegis/codex/harness.py` | the `Codex` harness object |
| `src/aegis/quota/codex.py` | the ChatGPT quota provider |
| `src/aegis/usage/prices.py` | gains OpenAI prices and `codex_prices_for` |
| `scripts/record_codex.py` | records fixtures from a real app-server |
| `tests/fixtures/codex/*.jsonl` | recorded lines |
| `tests/fake_codex.py` | a stand-in `codex app-server` |
| `tests/test_codex_stream.py`, `tests/test_codex_config.py`, `tests/test_session_codex.py`, `tests/test_quota_codex.py` | tests |
| `src/aegis/harness.py`, `agents.py`, `cli.py`, `app.py`, `registry.py`, `session.py`, `config_ops.py`, `doctor.py`, `transcript/entries.py`, `quota/__init__.py`, `usage/scan.py`, `usage/store.py` | the plumbing that names a harness |

---

### Task 1: Recorder and fixtures

**Files:**
- Create: `src/aegis/codex/__init__.py`
- Create: `src/aegis/codex/stream.py` (constants only in this task)
- Create: `scripts/record_codex.py`
- Create: `tests/fixtures/codex/{plain,tool,mcp,steer,interrupt,subagent,compact,patch}.jsonl` (written by the script)

**Interfaces:**
- Produces: `aegis.codex.stream.LABEL: str`, `STORED: frozenset[str]`, `DELTAS: frozenset[str]`, `OWN: str`. Fixture files whose lines are exactly what `CodexProcess` hands `Launch.on_line`: its own `{"method": "aegis/<x>", "params": …}` lines and app-server notifications whose `method` is in `STORED | DELTAS`.

- [ ] **Step 1: Create the package and the constants**

`src/aegis/codex/__init__.py`:

```python
```

(an empty file)

`src/aegis/codex/stream.py`:

```python
"""Codex's app-server notifications, one stdout line at a time, as typed events.

Measured on codex-cli 0.162.1 (spec ``2026-10-09-aegis-2-codex-harness-design.md``;
fixtures in ``tests/fixtures/codex/``, recorded by ``scripts/record_codex.py``).
"""

from __future__ import annotations

LABEL = "Codex"
# Lines CodexProcess writes itself, for what only a response carries: the
# version (``aegis/initialize``), the thread (``aegis/thread``), the model a
# turn was sent with (``aegis/turn``) and a slash command's line
# (``aegis/command``).
OWN = "aegis/"
# What the store keeps. Everything else on the stream (status changes,
# warnings, MCP startup, rate limits, remote control) carries nothing a reload
# needs.
STORED = frozenset(
    {
        "turn/started",
        "turn/completed",
        "item/started",
        "item/completed",
        "thread/tokenUsage/updated",
        "thread/name/updated",
        "turn/plan/updated",
    }
)
# Folded live, never stored: the item's ``item/completed`` carries the text.
DELTAS = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta",
    }
)
```

- [ ] **Step 2: Write the recorder**

`scripts/record_codex.py`:

```python
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
```

- [ ] **Step 3: Record**

Run (from the worktree, with the key the Workspace keeps):

```bash
OPENROUTER_API_KEY=$(cat /home/apiad/Workspace/.claude/openrouter.token) uv run python scripts/record_codex.py
```

Expected: eight `name: N lines` rows. A free model can 429; re-run a failed one with `--only NAME`, or pass `--model cohere/north-mini-code:free`.

- [ ] **Step 4: Check what the recordings hold**

```bash
for f in tests/fixtures/codex/*.jsonl; do echo "$f"; python3 -c "
import json,sys,collections
c=collections.Counter()
for l in open('$f'):
    m=json.loads(l); it=(m.get('params') or {}).get('item') or {}
    c[m['method']+('/'+it['type'] if it else '')]+=1
print(dict(c))"; done
```

Expected: `tool` has `item/completed/commandExecution`; `mcp` has `item/completed/mcpToolCall`; `steer` has two `item/completed/userMessage`; `interrupt` ends in `turn/completed`; `subagent` has `collabAgentToolCall` and lines whose `threadId` differs from the `aegis/thread` id; `compact` has `contextCompaction` and an `aegis/command` line. If `patch` holds no `fileChange` item (the model used the shell), delete `patch.jsonl`; Task 2 tests the patch mapping with an inline line instead. Check that no line holds the OpenRouter key: `grep -c sk-or tests/fixtures/codex/*.jsonl` prints 0 for every file.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/codex/__init__.py src/aegis/codex/stream.py scripts/record_codex.py tests/fixtures/codex/
git commit -m "test(codex): recorder and fixtures from codex-cli 0.162.1" -- src/aegis/codex/__init__.py src/aegis/codex/stream.py scripts/record_codex.py tests/fixtures/codex/
```

---

### Task 2: The parser

**Files:**
- Modify: `src/aegis/codex/stream.py`
- Modify: `src/aegis/transcript/entries.py:45,60` (register the parser)
- Create: `tests/test_codex_stream.py`

**Interfaces:**
- Consumes: Task 1's constants and fixtures; `aegis.claude.stream` events (`Init`, `Echo`, `Text`, `Thinking`, `Delta`, `ToolCall`, `ToolOutput`, `Step`, `Usage`, `Compact`, `Title`, `Result`, `Garbled`, `Ignored`, `Event`).
- Produces: `aegis.codex.stream.Parser` with `feed(line: str) -> list[Event]` and `end_turn() -> None`; `diff_pair(diff: str) -> tuple[str, str]`; `PARSERS["codex"]`. Task 7 replaces `Parser._price` (returns `None` here).

- [ ] **Step 1: Write the failing tests**

`tests/test_codex_stream.py`:

```python
import json
from pathlib import Path

import pytest

from aegis.claude.stream import Delta, Echo, Init, Result, Step, Text, ToolCall, Usage
from aegis.codex.stream import DELTAS, Parser, diff_pair
from aegis.transcript.entries import Fold

FIX = Path(__file__).parent / "fixtures" / "codex"


def lines(name: str) -> list[str]:
    return [ln for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln]


def first_prompt(name: str) -> str:
    for ln in lines(name):
        m = json.loads(ln)
        item = (m.get("params") or {}).get("item") or {}
        if m["method"] == "item/completed" and item.get("type") == "userMessage":
            return "".join(c.get("text", "") for c in item["content"] if c.get("type") == "text")
    raise AssertionError("no prompt")


def fold(name: str, *sends: str, interrupt_before: str | None = None) -> Fold:
    """Fold a fixture the way a session stores it; deltas go through ``live``."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="cx", model="m", cwd="/x")
    for s in sends or (first_prompt(name),):
        own("send", text=s)
    for ln in lines(name):
        if interrupt_before and interrupt_before in ln:
            own("interrupt")
            interrupt_before = None
        evs = f.parse("codex", ln)
        if json.loads(ln)["method"] in DELTAS:
            f.live(evs)
            continue
        f.apply({"i": i, "ts": 1.0 + i, "src": "codex", "line": ln}, evs)
        i += 1
    return f


def refold(name: str, *sends: str) -> list[dict]:
    f, i = Fold(), 0
    recs = [{"src": "aegis", "kind": "spawn", "agent": "cx", "model": "m", "cwd": "/x"}]
    recs += [{"src": "aegis", "kind": "send", "text": s} for s in sends or (first_prompt(name),)]
    recs += [{"src": "codex", "line": ln} for ln in lines(name)
             if json.loads(ln)["method"] not in DELTAS]  # fmt: skip
    for r in recs:
        f.apply({"i": i, "ts": 1.0 + i, **r})
        i += 1
    return f.entries()


def tools(f: Fold) -> list[dict]:
    return [e for e in f.entries() if e["kind"] == "tool"]


@pytest.mark.parametrize("name", ["plain", "tool", "mcp", "steer", "subagent"])
def test_live_deltas_end_where_a_reload_starts(name):
    assert fold(name).entries() == refold(name)


def test_a_plain_turn_names_codex_its_version_and_model():
    e = fold("plain").entries()
    assert any(x["summary"].startswith("Codex 0.162.1 · openrouter/") for x in e)
    assert [x["status"] for x in e if x["kind"] == "user"] == ["ok"]
    assert any(x["kind"] == "prose" and x["md"].strip() for x in e)
    assert e[-1]["summary"].startswith("done in")


def test_a_shell_call_is_a_bash_row_with_its_output():
    (t,) = tools(fold("tool"))
    assert (t["title"], t["status"]) == ("Bash", "ok")
    assert "hi" in t["detail"]["tail"]


def test_an_mcp_call_is_named_as_claude_names_it():
    (t,) = tools(fold("mcp"))
    assert t["status"] == "ok" and "tok-record" in t["detail"]["tail"]


def test_a_steered_prompt_is_read_in_the_same_turn():
    f = fold("steer", first_prompt("steer"), "Also say the word MANGO.")
    assert [e["status"] for e in f.entries() if e["kind"] == "user"] == ["ok", "ok"]
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 1


def test_an_interrupt_ends_the_turn_once_and_marks_the_call():
    f = fold("interrupt", interrupt_before='"turn/completed"')
    assert [t["status"] for t in tools(f)] == ["err"]
    assert [e["summary"] for e in f.entries() if e["kind"] == "error"] == ["interrupted"]


def test_a_subagent_is_a_task_whose_child_thread_counts_as_steps():
    (t,) = [e for e in fold("subagent").entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0


def test_a_compaction_is_shown_once():
    f = fold("compact", first_prompt("compact"), "/compact")
    assert sum(e["kind"] == "user" and e["md"] == "/compact" for e in f.entries()) == 1
    assert sum(e["summary"].startswith("done in") for e in f.entries()) == 2


def test_a_patch_update_is_an_edit_with_its_diff():
    thread = {"method": "aegis/thread", "params": {"thread": {"id": "t1", "model": "m", "modelProvider": "p"}}}
    change = {"path": "/x/a.txt", "kind": {"type": "update"}, "diff": "@@ -1 +1 @@\n-hello\n+bye\n"}
    item = {"type": "fileChange", "id": "fc1", "status": "completed", "changes": [change]}
    f = Fold()
    f.apply({"i": 0, "ts": 1.0, "src": "aegis", "kind": "spawn", "agent": "cx", "model": "m", "cwd": "/x"})
    for i, line in enumerate([thread,
                              {"method": "turn/started", "params": {"threadId": "t1", "turn": {"id": "u1"}}},
                              {"method": "item/completed", "params": {"threadId": "t1", "turnId": "u1", "item": item}}],
                             start=1):  # fmt: skip
        ln = json.dumps(line)
        f.apply({"i": i, "ts": 1.0 + i, "src": "codex", "line": ln}, f.parse("codex", ln))
    (t,) = tools(f)
    assert t["title"] == "Edit" and t["detail"]["diff"]["removed"] and t["detail"]["diff"]["added"]


def test_diff_pair_splits_a_unified_diff():
    assert diff_pair("@@ -1,2 +1,2 @@\n keep\n-old\n+new\n") == ("keep\nold", "keep\nnew")


def line(method: str, **params) -> str:
    return json.dumps({"method": method, "params": params})


def test_the_parser_maps_one_turn():
    p = Parser()
    p.feed(line("aegis/initialize", userAgent="aegis/0.162.1 (Ubuntu; x86_64)"))
    # A model no price table will ever hold, so Task 6's prices leave this test alone.
    assert p.feed(line("aegis/thread", thread={"id": "t1", "model": "gpt-test", "modelProvider": "openai"})) == [
        Init(session_id="t1", model="openai/gpt-test", version="0.162.1", harness="Codex")
    ]
    assert p.feed(line("turn/started", threadId="t1", turn={"id": "u1"})) == []
    user = {"type": "userMessage", "id": "m0", "content": [{"type": "text", "text": "hi"}]}
    assert p.feed(line("item/completed", threadId="t1", turnId="u1", item=user)) == [Echo(text="hi")]
    msg = {"type": "agentMessage", "id": "a1", "text": ""}
    assert p.feed(line("item/started", threadId="t1", turnId="u1", item=msg)) == [
        Text(text="", parent=None, usage=None, key="a1")
    ]
    assert p.feed(line("item/agentMessage/delta", threadId="t1", turnId="u1", itemId="a1", delta="he")) == [
        Delta(key="a1", kind="prose", text="he")
    ]
    usage = {"last": {"inputTokens": 1000, "cachedInputTokens": 400, "cacheWriteInputTokens": 0,
                      "outputTokens": 30, "reasoningOutputTokens": 10, "totalTokens": 1030},
             "total": {"inputTokens": 1000, "cachedInputTokens": 400, "outputTokens": 30,
                       "reasoningOutputTokens": 10, "totalTokens": 1030},
             "modelContextWindow": 258400}  # fmt: skip
    assert p.feed(line("thread/tokenUsage/updated", threadId="t1", turnId="u1", tokenUsage=usage)) == [
        Step(usage=Usage(input=600, cache_creation=0, cache_read=400, output=30), parent=None)
    ]
    done = {"id": "u1", "status": "completed", "durationMs": 1200, "error": None}
    assert p.feed(line("turn/completed", threadId="t1", turn=done)) == [
        Result(is_error=False, subtype="success", duration_ms=1200, cost_usd=None,
               stop_reason=None, context_window=258400)  # fmt: skip
    ]


def test_a_turn_of_a_thread_nobody_spawned_is_not_ours():
    p = Parser()
    p.feed(line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"}))
    msg = {"type": "agentMessage", "id": "a9", "text": "x"}
    assert p.feed(line("item/completed", threadId="other", turnId="u", item=msg)) == []


def test_a_command_line_is_echoed_once_and_its_user_message_is_not():
    p = Parser()
    p.feed(line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"}))
    assert p.feed(line("aegis/command", line="/review look at x", kind="review")) == [
        Echo(text="/review look at x")
    ]
    user = {"type": "userMessage", "id": "m1", "content": [{"type": "text", "text": "look at x"}]}
    assert p.feed(line("item/completed", threadId="t1", turnId="u", item=user)) == []


def test_a_shell_call_shows_the_command_codex_ran_not_its_wrapper():
    p = Parser()
    p.feed(line("aegis/thread", thread={"id": "t1", "model": "m", "modelProvider": "p"}))
    item = {"type": "commandExecution", "id": "c1", "command": "/bin/bash -lc 'ls -la'",
            "commandActions": [{"type": "unknown", "command": "ls -la"}], "status": "inProgress"}  # fmt: skip
    assert p.feed(line("item/started", threadId="t1", turnId="u", item=item)) == [
        ToolCall(id="c1", name="Bash", input={"command": "ls -la"}, parent=None, usage=None)
    ]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_codex_stream.py -q`
Expected: FAIL at import: `cannot import name 'Parser' from 'aegis.codex.stream'`.

- [ ] **Step 3: Write the parser**

Replace `src/aegis/codex/stream.py` with this module. Task 1's four constants are repeated in it unchanged, right after the imports.

```python
"""Codex's app-server notifications, one stdout line at a time, as typed events.

The events are the ones ``claude/stream.py`` defines, so the fold keeps one set
of rules for every harness. Measured on codex-cli 0.162.1 (spec
``2026-10-09-aegis-2-codex-harness-design.md``; fixtures in
``tests/fixtures/codex/``, recorded by ``scripts/record_codex.py``):

- A turn is ``turn/started`` … ``turn/completed``; every item is
  ``item/started`` then ``item/completed``, the latter carrying the whole item.
- Text streams as ``item/agentMessage/delta`` and reasoning as
  ``item/reasoning/textDelta`` (raw content) or ``summaryTextDelta``; the item
  id is the entry id, so the opening item, the deltas and the closing item
  touch one entry.
- ``thread/tokenUsage/updated`` follows each model request; its ``last``
  breakdown is that request, and ``outputTokens`` already holds the reasoning.
- A subagent is a ``collabAgentToolCall`` whose ``receiverThreadIds`` name a
  child thread; the child's own events arrive on the same stream and are steps
  of that call.
- No line names the model of a turn or the Codex version, so CodexProcess
  writes its own ``aegis/*`` lines for them (``OWN``).

The parser keeps what one line cannot carry alone (the root thread, the child
threads, the model, item kinds, open calls, the running cost), so it must see
every stored line in order, which is why the fold owns it. Every value comes
from the lines and none from the clock, so a fold of the store equals the live
fold.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..claude.stream import (
    Compact,
    Delta,
    Echo,
    Event,
    Garbled,
    Ignored,
    Init,
    Result,
    Step,
    Text,
    Thinking,
    Title,
    ToolCall,
    ToolOutput,
    Usage,
)

LABEL = "Codex"
OWN = "aegis/"
STORED = frozenset(
    {
        "turn/started",
        "turn/completed",
        "item/started",
        "item/completed",
        "thread/tokenUsage/updated",
        "thread/name/updated",
        "turn/plan/updated",
    }
)
DELTAS = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta",
    }
)

_VERSION = re.compile(r"/(\d+\.\d+\.\d+)")
_STATUS = {"inProgress": "in_progress"}


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def diff_pair(diff: str) -> tuple[str, str]:
    """A unified diff's (old, new) text, so an Edit row draws its diff window."""
    old: list[str] = []
    new: list[str] = []
    for ln in diff.splitlines():
        if ln.startswith(("@@", "---", "+++")):
            continue
        if ln.startswith("-"):
            old.append(ln[1:])
        elif ln.startswith("+"):
            new.append(ln[1:])
        else:
            body = ln[1:] if ln.startswith(" ") else ln
            old.append(body)
            new.append(body)
    return "\n".join(old), "\n".join(new)


def _usage(b: Any) -> Usage | None:
    """One request's tokens. Codex counts cached and written input inside
    ``inputTokens``; aegis counts them apart, so ``context`` is ``totalTokens``."""
    if not isinstance(b, dict):
        return None
    cached = int(b.get("cachedInputTokens") or 0)
    write = int(b.get("cacheWriteInputTokens") or 0)
    return Usage(
        input=max(0, int(b.get("inputTokens") or 0) - cached - write),
        cache_creation=write,
        cache_read=cached,
        output=int(b.get("outputTokens") or 0),
    )


def _user_text(item: dict) -> str:
    return "".join(
        str(c.get("text") or "")
        for c in item.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    )


def _shell(item: dict) -> str:
    """The command Codex ran, without its ``/bin/bash -lc`` wrapper when the
    item says what it was."""
    for a in item.get("commandActions") or []:
        if isinstance(a, dict) and isinstance(a.get("command"), str):
            return a["command"]
    return str(item.get("command") or "")


def _calls(item: dict) -> list[tuple[str, str, dict]]:
    """The tool rows one item makes: (call id, Claude's tool name, input)."""
    iid, kind = str(item.get("id") or ""), item.get("type")
    if kind == "commandExecution":
        return [(iid, "Bash", {"command": _shell(item)})]
    if kind == "mcpToolCall":
        name = f"mcp__{item.get('server')}__{item.get('tool')}"
        return [(iid, name, _dict(item.get("arguments")))]
    if kind == "webSearch":
        return [(iid, "WebSearch", {"query": str(item.get("query") or "")})]
    if kind == "collabAgentToolCall":
        prompt = str(item.get("prompt") or "")
        if item.get("tool") == "spawnAgent":
            return [(iid, "Task", {"description": prompt[:60], "prompt": prompt})]
        return [(iid, f"agent.{item.get('tool')}", {"description": str(item.get("tool"))})]
    if kind == "fileChange":
        changes = [c for c in item.get("changes") or [] if isinstance(c, dict)]
        out = []
        for n, ch in enumerate(changes):
            cid = iid if len(changes) == 1 else f"{iid}:{n}"
            path = str(ch.get("path") or "")
            old, new = diff_pair(str(ch.get("diff") or ""))
            how = _dict(ch.get("kind")).get("type")
            if how == "add":
                out.append((cid, "Write", {"file_path": path, "content": new}))
            elif how == "delete":
                out.append((cid, "Delete", {"file_path": path}))
            else:
                out.append((cid, "Edit", {"file_path": path, "old_string": old, "new_string": new}))
        return out
    return []


def _result(item: dict) -> tuple[str, bool]:
    """(output text, is_error) of a finished tool item."""
    kind, status = item.get("type"), item.get("status")
    if kind == "commandExecution":
        code = item.get("exitCode")
        failed = status != "completed" or (isinstance(code, int) and code != 0)
        return str(item.get("aggregatedOutput") or ""), failed
    if kind == "mcpToolCall":
        if status == "failed":
            return str(_dict(item.get("error")).get("message") or "failed"), True
        res = _dict(item.get("result"))
        text = "\n".join(
            str(c.get("text") or "")
            for c in res.get("content") or []
            if isinstance(c, dict) and c.get("type") == "text"
        )
        return text, False
    return str(status or ""), status == "failed"


class Parser:
    def __init__(self) -> None:
        self.root: str | None = None
        self.children: dict[str, str] = {}  # child thread -> its Task call
        self.task_call: str | None = None
        self.version: str | None = None
        self.model: str | None = None
        self.title: str | None = None
        self.kinds: dict[str, str] = {}  # item id -> "prose" | "thinking"
        self.open_calls: set[str] = set()
        self.closed_calls: set[str] = set()
        self.cost: float | None = None
        self.window: int | None = None
        self.skip_echo = False
        self.plans = 0
        self.turn_open = False

    def end_turn(self) -> None:
        """The turn ended (its ``turn/completed``, or for the fold an exit, a
        stop or a server restart): nothing of it may reach the next."""
        self.closed_calls |= self.open_calls
        self.open_calls.clear()
        self.turn_open = False
        self.skip_echo = False

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        method = str(obj.get("method"))
        handler = getattr(self, HANDLERS.get(method, ""), None)
        if handler is None:
            return [Ignored(type=method)]
        return handler(_dict(obj.get("params")))

    # -- whose line ----------------------------------------------------
    def _whose(self, tid: Any) -> tuple[bool, str | None]:
        """Whether the line is this conversation's, and the Task call it is a
        step of (None for the conversation itself). A thread nobody named is a
        subagent of the last spawn, or nobody's."""
        if not isinstance(tid, str):
            return False, None
        if self.root is None:
            self.root = tid
        if tid == self.root:
            return True, None
        if tid not in self.children:
            if self.task_call is None:
                return False, None
            self.children[tid] = self.task_call
        return True, self.children[tid]

    # -- aegis's own lines ---------------------------------------------
    def _initialize(self, p: dict) -> list[Event]:
        m = _VERSION.search(str(p.get("userAgent") or ""))
        self.version = m.group(1) if m else None
        return []

    def _thread(self, p: dict) -> list[Event]:
        th = _dict(p.get("thread"))
        tid = _str(th.get("id"))
        provider, model = _str(th.get("modelProvider")), _str(th.get("model"))
        self.root, self.children, self.task_call = tid, {}, None
        self.model = f"{provider}/{model}" if provider and model else None
        return [Init(session_id=tid, model=self.model, version=self.version, harness=LABEL)]

    def _turn_settings(self, p: dict) -> list[Event]:
        model = _str(p.get("model"))
        if not model or model == self.model:
            return []
        self.model = model
        return [Init(session_id=self.root, model=model, version=self.version, harness=LABEL)]

    def _command(self, p: dict) -> list[Event]:
        """A slash command, shown as typed. Its turn's user message, if any,
        is the expanded input and is not echoed again."""
        self.skip_echo = True
        return [Echo(text=str(p.get("line") or ""))]

    # -- turns -----------------------------------------------------------
    def _turn_started(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        if ok and parent is None:
            self.turn_open = True
        return []

    def _turn_completed(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        if not ok or parent is not None or not self.turn_open:
            return []
        turn = _dict(p.get("turn"))
        status = turn.get("status")
        if status == "completed":
            subtype = "success"
        elif status == "interrupted":
            subtype = "interrupted"
        else:
            subtype = str(_dict(turn.get("error")).get("message") or status or "error")[:120]
        span = turn.get("durationMs")
        result = Result(
            is_error=status != "completed",
            subtype=subtype,
            duration_ms=span if isinstance(span, int) else None,
            cost_usd=self.cost,
            stop_reason=None,
            context_window=self.window,
        )
        self.end_turn()
        return [result]

    # -- items -----------------------------------------------------------
    def _item_started(self, p: dict) -> list[Event]:
        return self._item(p, done=False)

    def _item_completed(self, p: dict) -> list[Event]:
        return self._item(p, done=True)

    def _item(self, p: dict, *, done: bool) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        item = _dict(p.get("item"))
        if not ok:
            return []
        kind, iid = item.get("type"), str(item.get("id") or "")
        if kind == "userMessage":
            if not done or parent is not None:
                return []
            if self.skip_echo:
                self.skip_echo = False
                return []
            text = _user_text(item)
            return [Echo(text=text)] if text.strip() else []
        if kind == "agentMessage":
            self.kinds[iid] = "prose"
            return [Text(text=str(item.get("text") or ""), parent=parent, usage=None, key=iid)]
        if kind == "reasoning":
            self.kinds[iid] = "thinking"
            text = "\n\n".join(str(c) for c in item.get("content") or []) or "\n\n".join(
                str(s) for s in item.get("summary") or []
            )
            return [Thinking(text=text, parent=parent, usage=None, key=iid)]
        if kind == "contextCompaction":
            return [Compact(pre_tokens=0, post_tokens=0)] if done and parent is None else []
        return self._tool(item, parent, done)

    def _tool(self, item: dict, parent: str | None, done: bool) -> list[Event]:
        out: list[Event] = []
        for cid, name, inp in _calls(item):
            if cid in self.closed_calls:
                continue  # its turn already ended: an interrupt's late item
            if cid not in self.open_calls:
                self.open_calls.add(cid)
                out.append(ToolCall(id=cid, name=name, input=inp, parent=parent, usage=None))
                if name == "Task" and parent is None:
                    self.task_call = cid
            if done:
                self.open_calls.discard(cid)
                self.closed_calls.add(cid)
                text, err = _result(item)
                out.append(ToolOutput(id=cid, text=text, is_error=err, parent=parent))
        if item.get("type") == "collabAgentToolCall" and item.get("tool") == "spawnAgent":
            for t in item.get("receiverThreadIds") or []:
                if isinstance(t, str) and t != self.root:
                    self.children.setdefault(t, str(item.get("id")))
        return out

    def _plan(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        if not ok:
            return []
        self.plans += 1
        cid = f"plan:{p.get('turnId')}:{self.plans}"
        todos = [
            {
                "content": str(s.get("step") or ""),
                "status": _STATUS.get(str(s.get("status")), str(s.get("status"))),
                "activeForm": str(s.get("step") or ""),
            }
            for s in p.get("plan") or []
            if isinstance(s, dict)
        ]
        return [
            ToolCall(id=cid, name="TodoWrite", input={"todos": todos}, parent=parent, usage=None),
            ToolOutput(id=cid, text="", is_error=False, parent=parent),
        ]

    # -- usage, names, deltas ----------------------------------------
    def _price(self, u: Usage) -> float | None:
        """What one request cost, or None when the model has no price (Task 7)."""
        return None

    def _token_usage(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        tu = _dict(p.get("tokenUsage"))
        u = _usage(tu.get("last"))
        if not ok or u is None:
            return []
        if parent is None and isinstance(tu.get("modelContextWindow"), int):
            self.window = tu["modelContextWindow"]
        cost = self._price(u)
        if cost is not None:
            self.cost = (self.cost or 0.0) + cost
        return [Step(usage=u, parent=parent)]

    def _name(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        name = _str(p.get("threadName"))
        if not ok or parent is not None or not name or name == self.title:
            return []
        self.title = name
        return [Title(text=name)]

    def _delta(self, p: dict) -> list[Event]:
        """Always one Delta, so the session never stores a delta line; one
        with no key (a child's, or an item not seen opening) draws nothing."""
        ok, parent = self._whose(p.get("threadId"))
        iid = str(p.get("itemId") or "")
        kind = self.kinds.get(iid)
        if not ok or parent is not None or kind is None:
            return [Delta(key="", kind="", text="")]
        return [Delta(key=iid, kind=kind, text=str(p.get("delta") or ""))]


HANDLERS = {
    "aegis/initialize": "_initialize",
    "aegis/thread": "_thread",
    "aegis/turn": "_turn_settings",
    "aegis/command": "_command",
    "turn/started": "_turn_started",
    "turn/completed": "_turn_completed",
    "item/started": "_item_started",
    "item/completed": "_item_completed",
    "turn/plan/updated": "_plan",
    "thread/tokenUsage/updated": "_token_usage",
    "thread/name/updated": "_name",
    "item/agentMessage/delta": "_delta",
    "item/reasoning/textDelta": "_delta",
    "item/reasoning/summaryTextDelta": "_delta",
}
```

In `src/aegis/transcript/entries.py`, import the parser next to OpenCode's and register it:

```python
from ..codex.stream import Parser as CodexParser
from ..opencode.stream import Parser as OpenCodeParser
```

```python
PARSERS: dict[str, Any] = {
    "claude": _Stateless,
    "opencode": OpenCodeParser,
    "codex": CodexParser,
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_codex_stream.py tests/test_opencode_stream.py -q`
Expected: PASS. If `test_an_interrupt_ends_the_turn_once_and_marks_the_call` sees the command item completed before `turn/completed` in the recording (so the tool row is `ok`), keep the parser and change the assertion to what the recording shows, with a comment naming the order Codex used; the fold's interrupt flag still shows `interrupted`. If `patch.jsonl` was deleted in Task 1, the inline patch test is the only patch test, which is enough.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(codex): parse app-server notifications into the fold's events" -- src/aegis/codex/stream.py src/aegis/transcript/entries.py tests/test_codex_stream.py
```

---

### Task 3: What a child starts with, and the catalog

**Files:**
- Create: `src/aegis/codex/config.py`
- Create: `tests/test_codex_config.py`

**Interfaces:**
- Consumes: `aegis.claude.control.Catalog`, `Model`, `_doc`; `aegis.mcp.HEADER` (`"X-Aegis-Session"`).
- Produces: `TOKEN_ENV = "AEGIS_SESSION_TOKEN"`; `SANDBOX_MODE: dict[str, str]`; `SANDBOX_POLICY: dict[str, dict]`; `split_model(model: str) -> tuple[str, str]`; `argv(bin: str, mcp_url: str | None) -> list[str]`; `child_env(base: Mapping[str, str], mcp: tuple[str, str] | None) -> dict[str, str]`; `provider_model(provider: str, m: dict) -> Model | None`; `catalog_from(model_list: list, skills: list, listed: list[Model], current: str) -> Catalog`.

- [ ] **Step 1: Write the failing tests**

`tests/test_codex_config.py`:

```python
from aegis.claude.control import Model
from aegis.codex.config import (
    PERMISSIONS,
    SANDBOX_MODE,
    SANDBOX_POLICY,
    TOKEN_ENV,
    argv,
    catalog_from,
    child_env,
    provider_model,
    split_model,
)


def test_a_model_splits_on_its_first_slash():
    assert split_model("openrouter/nvidia/nemotron:free") == ("openrouter", "nvidia/nemotron:free")
    assert split_model("openai/gpt-5.5") == ("openai", "gpt-5.5")
    assert split_model("") == ("", "")


def test_argv_overrides_come_after_the_subcommand():
    a = argv("codex", "http://127.0.0.1:9/mcp")
    assert a[:2] == ["codex", "app-server"]
    assert a[2:6] == ["--disable", "plugins", "--disable", "remote_plugin"]
    joined = " ".join(a)
    assert 'mcp_servers.aegis.url="http://127.0.0.1:9/mcp"' in joined
    assert f'mcp_servers.aegis.env_http_headers={{"X-Aegis-Session"="{TOKEN_ENV}"}}' in joined
    assert 'mcp_servers.aegis.default_tools_approval_mode="approve"' in joined
    assert f'shell_environment_policy.exclude=["{TOKEN_ENV}"]' in joined
    assert a[-2:] == ["-c", 'approval_policy="never"']


def test_no_token_ever_rides_on_argv_and_no_mcp_means_no_aegis_flags():
    assert not any("tok-" in x for x in argv("codex", "http://h/mcp"))
    assert not any("mcp_servers" in x for x in argv("codex", None))


def test_the_token_is_only_in_the_environment_and_only_with_mcp():
    assert child_env({"PATH": "/bin", TOKEN_ENV: "stale"}, ("http://h/mcp", "tok-1")) == {
        "PATH": "/bin", TOKEN_ENV: "tok-1",
    }  # fmt: skip
    assert child_env({"PATH": "/bin", TOKEN_ENV: "stale"}, None) == {"PATH": "/bin"}


def test_each_permission_allows_strictly_more_than_the_one_before():
    assert list(SANDBOX_MODE) == list(PERMISSIONS) == ["read", "write", "auto", "full"]
    assert SANDBOX_POLICY["read"] == {"type": "readOnly", "networkAccess": False}
    assert SANDBOX_POLICY["write"] == {"type": "workspaceWrite", "networkAccess": False}
    assert SANDBOX_POLICY["auto"] == {"type": "workspaceWrite", "networkAccess": True}
    assert SANDBOX_POLICY["full"] == {"type": "dangerFullAccess"}
    assert SANDBOX_MODE == {"read": "read-only", "write": "workspace-write",
                            "auto": "workspace-write", "full": "danger-full-access"}  # fmt: skip


def test_the_catalog_has_codex_models_skills_the_provider_and_the_current_model():
    listed = [
        {"id": "gpt-5.5", "displayName": "GPT-5.5", "hidden": False,
         "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
        {"id": "secret", "displayName": "x", "hidden": True, "supportedReasoningEfforts": []},
    ]  # fmt: skip
    skills = [{"name": "imagegen", "description": "Make images. Long text.", "path": "/s/imagegen"}]
    nemo = provider_model("openrouter", {"id": "nvidia/n:free", "name": "Nemotron", "context_length": 262144,
                                         "pricing": {"prompt": "0", "completion": "0"},
                                         "supported_parameters": ["tools"]})  # fmt: skip
    cat = catalog_from(listed, skills, [nemo], "openrouter/other:free")
    values = [m.value for m in cat.models]
    assert values == ["openai/gpt-5.5", "openrouter/nvidia/n:free", "openrouter/other:free"]
    assert cat.model("openai/gpt-5.5").efforts == ("low", "high")
    assert cat.model("openrouter/nvidia/n:free").window == 262144
    assert cat.model("openrouter/nvidia/n:free").free is True
    assert [c["name"] for c in cat.commands] == ["compact", "review", "imagegen"]
    assert cat.commands[2] == {"name": "imagegen", "hint": "", "doc": "Make images.", "source": "skill"}


def test_a_provider_entry_without_an_id_is_skipped():
    assert provider_model("p", {"name": "x"}) is None
    assert isinstance(provider_model("p", {"id": "m"}), Model)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_codex_config.py -q`
Expected: FAIL at import: `No module named 'aegis.codex.config'`.

- [ ] **Step 3: Write the module**

`src/aegis/codex/config.py`:

```python
"""What a Codex child is started with, and its catalog. No I/O here.

The child reads ``~/.codex/config.toml`` (the person's login, providers and
skills) and aegis adds its own settings with ``-c``, which override the file.
Measured on codex-cli 0.162.1 (spec ``2026-10-09-aegis-2-codex-harness-design.md``):

- The token travels in ``AEGIS_SESSION_TOKEN``, named by ``env_http_headers``,
  because argv is readable by every user through ``ps``. Codex's default shell
  policy hands that variable to the model's shell, so it is excluded there.
- Under ``approval_policy="never"`` an MCP call fails unless its server says
  ``default_tools_approval_mode="approve"``.
- The plugin features clone a marketplace on every start, and that ``git
  fetch`` outlived the server and held the thread's writer lease.
- aegis has no approval prompt, so nothing asks: the four permissions are
  sandbox policies, each allowing strictly more than the one before, so a
  session an agent spawns still has at most the agent's power.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..claude.control import Catalog, Model, _doc

TOKEN_ENV = "AEGIS_SESSION_TOKEN"
DISABLED = ("plugins", "remote_plugin")
PERMISSIONS = ("read", "write", "auto", "full")
SANDBOX_MODE = {
    "read": "read-only",
    "write": "workspace-write",
    "auto": "workspace-write",
    "full": "danger-full-access",
}
SANDBOX_POLICY: dict[str, dict[str, Any]] = {
    "read": {"type": "readOnly", "networkAccess": False},
    "write": {"type": "workspaceWrite", "networkAccess": False},
    "auto": {"type": "workspaceWrite", "networkAccess": True},
    "full": {"type": "dangerFullAccess"},
}
BUILTIN = (
    ("compact", "Summarize the conversation to free context."),
    ("review", "Review the uncommitted changes, or what the arguments ask."),
)


def split_model(model: str) -> tuple[str, str]:
    """``provider/model`` as (provider, model); the model keeps its slashes."""
    provider, _, model_id = model.partition("/")
    return provider, model_id


def argv(bin: str, mcp_url: str | None) -> list[str]:
    out = [bin, "app-server"]
    for feature in DISABLED:
        out += ["--disable", feature]
    if mcp_url is not None:
        from ..mcp import HEADER

        out += [
            "-c", f"mcp_servers.aegis.url={json.dumps(mcp_url)}",
            "-c", f'mcp_servers.aegis.env_http_headers={{"{HEADER}"="{TOKEN_ENV}"}}',
            "-c", 'mcp_servers.aegis.default_tools_approval_mode="approve"',
            "-c", f'shell_environment_policy.exclude=["{TOKEN_ENV}"]',
        ]  # fmt: skip
    return out + ["-c", 'approval_policy="never"']


def child_env(base: Mapping[str, str], mcp: tuple[str, str] | None) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k != TOKEN_ENV}
    if mcp is not None:
        env[TOKEN_ENV] = mcp[1]
    return env


def provider_model(provider: str, m: dict) -> Model | None:
    """One entry of an OpenAI-compatible ``/models`` list (OpenRouter's has
    ``context_length``, ``pricing`` and ``supported_parameters``)."""
    mid = m.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    value = f"{provider}/{mid}"
    window = m.get("context_length")
    pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
    params = m.get("supported_parameters") or []
    return Model(
        value=value,
        resolved=value,
        label=str(m.get("name") or mid),
        doc="",
        efforts=(),
        window=window if isinstance(window, int) else None,
        free=pricing.get("prompt") == "0"
        and pricing.get("completion") == "0"
        and "tools" in params,
    )


def catalog_from(model_list: list, skills: list, listed: list[Model], current: str) -> Catalog:
    """Codex's own models (``model/list``, the ``openai`` provider), the
    current provider's ``/models``, and the session's model if neither has it,
    so ``/model`` can always switch back."""
    commands = [
        {"name": name, "hint": "", "doc": doc, "source": "codex"} for name, doc in BUILTIN
    ]
    for s in skills:
        if isinstance(s, dict) and s.get("name"):
            commands.append(
                {
                    "name": str(s["name"]),
                    "hint": "",
                    "doc": _doc(str(s.get("description") or "")),
                    "source": "skill",
                }
            )
    models: list[Model] = []
    for m in model_list:
        if not isinstance(m, dict) or m.get("hidden") or not m.get("id"):
            continue
        value = f"openai/{m['id']}"
        efforts = tuple(
            str(o["reasoningEffort"])
            for o in m.get("supportedReasoningEfforts") or []
            if isinstance(o, dict) and o.get("reasoningEffort")
        )
        models.append(
            Model(
                value=value,
                resolved=value,
                label=str(m.get("displayName") or m["id"]),
                doc="",
                efforts=efforts,
            )
        )
    models += listed
    if current and not any(m.value == current for m in models):
        models.append(Model(value=current, resolved=current, label=current, doc="", efforts=()))
    return Catalog(tuple(commands), tuple(models))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_codex_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(codex): child argv, environment, sandbox table and catalog" -- src/aegis/codex/config.py tests/test_codex_config.py
```

---

### Task 4: The fake app-server, the process, and the harness in every place that names one

**Files:**
- Create: `tests/fake_codex.py`
- Modify: `tests/conftest.py` (a `fake_codex` fixture next to `fake_opencode`, lines 28-41)
- Create: `src/aegis/codex/process.py`
- Create: `src/aegis/codex/harness.py`
- Modify: `src/aegis/harness.py:82-91` (`harness_for` gains `codex_bin`)
- Modify: `src/aegis/agents.py:32,42-50` (`codex` in `HARNESSES`, its model rule)
- Modify: `src/aegis/session.py:185,212` (`codex_bin`)
- Modify: `src/aegis/registry.py:46,58,63,192` (`codex_bin`)
- Modify: `src/aegis/app.py:174,181,197,238` (`codex_bin`, `_bin`)
- Modify: `src/aegis/config_ops.py:36` (the bins map)
- Modify: `src/aegis/cli.py:62,134,155,217,290,308,325-326,345,354,444-446,469` (`--codex`)
- Modify: `src/aegis/doctor.py:45,84` (label and bins)
- Create: `tests/test_session_codex.py`

**Interfaces:**
- Consumes: Task 2's `STORED`, `DELTAS`, `LABEL`; Task 3's `argv`, `child_env`, `split_model`, `SANDBOX_MODE`, `SANDBOX_POLICY`, `catalog_from`, `provider_model`; `aegis.harness.Launch`.
- Produces: `CodexProcess(bin: str, launch: Launch)` satisfying `harness.Process`; `RpcError(Exception)`; `probe(bin, cwd, stderr_path, model="") -> Catalog`; `Codex` harness (`name="codex"`, `src="codex"`, `label="Codex"`, `tool_prefix="mcp__aegis__"`); `harness_for(name, claude_bin, opencode_bin, codex_bin="codex")`; `Session(..., codex_bin="codex")`; `Registry(..., codex_bin="codex")`; `App(..., codex_bin="codex")`; `aegis serve/doctor/init --codex`. Task 5 adds `send` routing for `/` lines, steer and `set`; this task's `send` already starts and steers turns.

- [ ] **Step 1: Write the fake**

`tests/fake_codex.py`:

```python
"""A stand-in for ``codex app-server``, for tests and the bench.

It speaks JSON-RPC on stdio in the shapes codex-cli 0.162.1 emits
(``tests/fixtures/codex/``). Its scripts are skills (``skills/list`` lists
them, a ``{"type": "skill"}`` input runs one with the text after it as its
argument), because aegis sends a ``/name`` line as a skill:

    sleep N        a shell call that takes N seconds, then text. A steer
                   meanwhile is read after the call, in the same turn.
    stream N       N words of text, one delta every 0.25 s.
    fail           a shell call that exits 1.
    bash D => OUT  a shell call of command D whose output is OUT.
    patch P        a file change of P from "a" to "b" (nothing is touched).
    mcp T JSON     call tool T of the aegis MCP server named on argv, with the
                   token from AEGIS_SESSION_TOKEN.
    spawn          a spawnAgent call whose child thread says one thing.
    big N          a shell call whose output is N kilobytes on one line.
    ask            a server request (an approval) before the text.
    recall         text listing the prompts this thread received before.
    argv           text "argv: <JSON of argv>".
    env            text "env: <AEGIS_SESSION_TOKEN or none>".
    body           text "body: <JSON of the last turn/start params>".
    exit N         a stderr line, then exit with code N.

A plain prompt is answered "you said: <prompt>". Text streams as an opening
item, three deltas and the closing item. Each turn reports 1,030 tokens
(1,000 in, 30 out) and a context window of 258,400. ``model/list`` has
``fake-pro`` (efforts low, medium, high) and ``fake-flash``.

``FAKE_CODEX_HOME`` keeps ``<thread>.prompts`` (what ``recall`` and
``thread/resume`` read) and ``<thread>.lease``: opening a thread starts a
``sleep`` grandchild in the fake's process group and writes its pid there, as
Codex's plugin clone held the thread. A resume while that pid lives fails with
"already has an active writer"; a clean exit (stdin closed) ends it.
``FAKE_CODEX_LOG=<file>`` gets ``START <pid>``, ``LEASE <pid>`` and one line
per request method. ``FAKE_CODEX_HANG=1`` ignores a closed stdin, so only a
signal ends it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

HOME = os.environ.get("FAKE_CODEX_HOME") or "/tmp/fake-codex-home"
LOG = os.environ.get("FAKE_CODEX_LOG")
VERSION = "0.162.1"
SCRIPTS = ("sleep", "stream", "fail", "bash", "patch", "mcp", "spawn", "big", "ask",
           "recall", "argv", "env", "body", "exit")  # fmt: skip
OUT = threading.Lock()
STATE: dict = {"thread": None, "turn": None, "steer": [], "stop": threading.Event(),
               "lease": None, "body": None, "model": "", "provider": ""}  # fmt: skip


def log(text: str) -> None:
    if LOG:
        with open(LOG, "a") as f:
            f.write(text + "\n")


def send(obj: dict) -> None:
    with OUT:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def note(method: str, **params) -> None:
    send({"method": method, "params": params})


def mcp_url() -> str | None:
    for a in sys.argv:
        if a.startswith("mcp_servers.aegis.url="):
            return json.loads(a.split("=", 1)[1])
    return None


def mcp_call(tool: str, arguments: dict) -> tuple[bool, str]:
    url = mcp_url()
    if url is None:
        return False, "no aegis MCP server configured"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}})  # fmt: skip
    req = urllib.request.Request(url, data=body.encode(), headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        "X-Aegis-Session": os.environ.get("AEGIS_SESSION_TOKEN", "")})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            reply = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if "error" in reply:
        return False, json.dumps(reply["error"])
    res = reply.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    return not res.get("isError"), text


# -- threads and leases ----------------------------------------------------
def prompts_file(tid: str) -> str:
    return os.path.join(HOME, f"{tid}.prompts")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def take_lease(tid: str) -> None:
    path = os.path.join(HOME, f"{tid}.lease")
    if os.path.exists(path):
        pid = int(open(path).read() or 0)
        if pid and alive(pid):
            raise ValueError(f"thread {tid} already has an active writer")
    child = subprocess.Popen(["sleep", "3600"])
    STATE["lease"] = child
    with open(path, "w") as f:
        f.write(str(child.pid))
    log(f"LEASE {child.pid}")


def drop_lease() -> None:
    child = STATE["lease"]
    if child is not None:
        child.kill()
        child.wait()
    if STATE["thread"]:
        try:
            os.remove(os.path.join(HOME, f"{STATE['thread']}.lease"))
        except FileNotFoundError:
            pass


def thread_result(tid: str) -> dict:
    thread = {"id": tid, "model": STATE["model"], "modelProvider": STATE["provider"],
              "cwd": os.getcwd(), "preview": "", "ephemeral": False}  # fmt: skip
    return {"thread": thread, "model": STATE["model"], "modelProvider": STATE["provider"],
            "reasoningEffort": None, "approvalPolicy": "never"}  # fmt: skip


def open_thread(params: dict, tid: str | None) -> dict:
    STATE["model"] = params.get("model") or "fake-pro"
    STATE["provider"] = params.get("modelProvider") or "openai"
    if tid is not None and not os.path.exists(prompts_file(tid)):
        raise LookupError(f"no rollout found for thread id {tid}")
    tid = tid or str(uuid.uuid4())
    take_lease(tid)
    STATE["thread"] = tid
    open(prompts_file(tid), "a").close()
    return thread_result(tid)


# -- turns -------------------------------------------------------------------
class Turn:
    def __init__(self, tid: str) -> None:
        self.tid, self.id, self.t0 = tid, str(uuid.uuid4()), time.time()

    def item(self, item: dict, *, started: bool = True) -> None:
        if started:
            note("item/started", threadId=self.tid, turnId=self.id, item={**item, "status": "inProgress"})
        note("item/completed", threadId=self.tid, turnId=self.id, item=item)

    def user(self, text: str) -> None:
        with open(prompts_file(self.tid), "a") as f:
            f.write(text.replace("\n", " ") + "\n")
        self.item({"type": "userMessage", "id": f"um-{uuid.uuid4().hex[:8]}",
                   "content": [{"type": "text", "text": text, "text_elements": []}]})  # fmt: skip

    def say(self, text: str, delay: float = 0.0) -> None:
        iid = f"msg-{uuid.uuid4().hex[:8]}"
        note("item/started", threadId=self.tid, turnId=self.id, item={"type": "agentMessage", "id": iid, "text": ""})
        words = text.split(" ")
        chunks = [" ".join(words[i::3]) for i in range(3)] if delay == 0 else [w + " " for w in words]
        for c in chunks:
            if delay:
                time.sleep(delay)
            note("item/agentMessage/delta", threadId=self.tid, turnId=self.id, itemId=iid, delta=c)
        full = text if delay == 0 else "".join(chunks)
        note("item/completed", threadId=self.tid, turnId=self.id, item={"type": "agentMessage", "id": iid, "text": full})

    def shell(self, command: str, output: str, code: int = 0, wait: float = 0.0) -> bool:
        """A shell call; False when an interrupt ended it."""
        iid = f"call-{uuid.uuid4().hex[:8]}"
        item = {"type": "commandExecution", "id": iid, "command": f"/bin/bash -lc '{command}'",
                "commandActions": [{"type": "unknown", "command": command}], "cwd": os.getcwd()}  # fmt: skip
        note("item/started", threadId=self.tid, turnId=self.id, item={**item, "status": "inProgress"})
        if STATE["stop"].wait(wait):
            return False
        note("item/completed", threadId=self.tid, turnId=self.id, item={
            **item, "status": "completed" if code == 0 else "failed", "exitCode": code,
            "aggregatedOutput": output, "durationMs": int(wait * 1000)})  # fmt: skip
        return True

    def usage(self) -> None:
        last = {"inputTokens": 1000, "cachedInputTokens": 0, "cacheWriteInputTokens": 0,
                "outputTokens": 30, "reasoningOutputTokens": 0, "totalTokens": 1030}  # fmt: skip
        note("thread/tokenUsage/updated", threadId=self.tid, turnId=self.id,
             tokenUsage={"last": last, "total": last, "modelContextWindow": 258400})  # fmt: skip

    def end(self, status: str = "completed") -> None:
        STATE["turn"] = None
        note("turn/completed", threadId=self.tid, turn={
            "id": self.id, "status": status, "error": None, "items": [],
            "durationMs": int((time.time() - self.t0) * 1000)})  # fmt: skip


def steered(t: Turn) -> list[str]:
    out, STATE["steer"] = STATE["steer"], []
    for text in out:
        t.user(text)
    return out


def run(t: Turn, name: str, args: str, text: str) -> None:
    note("turn/started", threadId=t.tid, turn={"id": t.id, "status": "inProgress", "items": []})
    t.user(text if name == "say" else (f"{args}" if args else name))
    extra = ""
    if name == "sleep":
        if not t.shell(f"sleep {args}", "", wait=float(args or 1)):
            t.end("interrupted")
            return
        got = steered(t)
        extra = "".join(f" also: {s}" for s in got)
        t.say(f"slept{extra}")
    elif name == "stream":
        t.say(" ".join(f"chunk{i + 1}" for i in range(int(args or 3))), delay=0.25)
    elif name == "fail":
        t.shell("false", "boom", code=1)
        t.say("it failed")
    elif name == "bash":
        cmd, _, out = args.partition(" => ")
        t.shell(cmd, out)
        t.say("ran it")
    elif name == "big":
        t.shell("cat big", "x" * (int(args or 100) * 1024))
        t.say("big done")
    elif name == "patch":
        change = {"path": args, "kind": {"type": "update"}, "diff": "@@ -1 +1 @@\n-a\n+b\n"}
        t.item({"type": "fileChange", "id": f"fc-{uuid.uuid4().hex[:8]}", "status": "completed",
                "changes": [change]})  # fmt: skip
        t.say("patched")
    elif name == "mcp":
        tool, _, raw = args.partition(" ")
        ok, out = mcp_call(tool, json.loads(raw or "{}"))
        item = {"type": "mcpToolCall", "id": f"mcp-{uuid.uuid4().hex[:8]}", "server": "aegis", "tool": tool,
                "arguments": json.loads(raw or "{}"), "status": "completed" if ok else "failed",
                "result": {"content": [{"type": "text", "text": out}]} if ok else None,
                "error": None if ok else {"message": out}}  # fmt: skip
        t.item(item)
        t.say("called it")
    elif name == "spawn":
        child = str(uuid.uuid4())
        call = {"type": "collabAgentToolCall", "id": f"col-{uuid.uuid4().hex[:8]}", "tool": "spawnAgent",
                "prompt": "say hi", "senderThreadId": t.tid, "receiverThreadIds": [child],
                "status": "completed"}  # fmt: skip
        t.item(call)
        sub = Turn(child)
        note("turn/started", threadId=child, turn={"id": sub.id, "status": "inProgress", "items": []})
        sub.say("child says hi")
        sub.usage()
        sub.end()
        t.say("the child said hi")
    elif name == "ask":
        send({"id": 900, "method": "item/commandExecution/requestApproval",
              "params": {"threadId": t.tid, "turnId": t.id, "command": "rm -rf /"}})  # fmt: skip
        t.say("asked")
    elif name == "recall":
        before = open(prompts_file(t.tid)).read().splitlines()[:-1]
        t.say("recall: " + " | ".join(before))
    elif name == "argv":
        t.say("argv: " + json.dumps(sys.argv[1:]))
    elif name == "env":
        t.say("env: " + os.environ.get("AEGIS_SESSION_TOKEN", "none"))
    elif name == "body":
        t.say("body: " + json.dumps(STATE["body"]))
    elif name == "exit":
        sys.stderr.write("fake codex is exiting\n")
        sys.stderr.flush()
        os._exit(int(args or 1))
    else:
        t.say(f"you said: {text}")
    t.usage()
    t.end()


def turn_input(items: list) -> tuple[str, str, str]:
    """(script, its argument, the text) from a turn's input items."""
    skill = next((i for i in items if i.get("type") == "skill"), None)
    text = " ".join(i.get("text", "") for i in items if i.get("type") == "text").strip()
    if skill is not None and skill.get("name") in SCRIPTS:
        return skill["name"], text, f"/{skill['name']} {text}".strip()
    return "say", "", text


# -- requests ------------------------------------------------------------------
def handle(msg: dict) -> dict | None:
    method, p = msg.get("method"), msg.get("params") or {}
    log(str(method))
    if method == "initialize":
        return {"userAgent": f"{p['clientInfo']['name']}/{VERSION} (fake)", "codexHome": HOME,
                "platformFamily": "unix", "platformOs": "linux"}  # fmt: skip
    if method == "thread/start":
        return open_thread(p, None)
    if method == "thread/resume":
        return open_thread(p, p["threadId"])
    if method == "model/list":
        return {"data": [
            {"id": "fake-pro", "displayName": "Fake Pro", "hidden": False,
             "supportedReasoningEfforts": [{"reasoningEffort": e, "description": e} for e in ("low", "medium", "high")]},
            {"id": "fake-flash", "displayName": "Fake Flash", "hidden": False, "supportedReasoningEfforts": []},
        ], "nextCursor": None}  # fmt: skip
    if method == "skills/list":
        return {"data": [{"cwd": os.getcwd(), "skills": [
            {"name": s, "description": f"The fake's {s} script.", "path": f"/fake/{s}/SKILL.md"} for s in SCRIPTS]}]}  # fmt: skip
    if method == "config/read":
        return {"config": {"model_providers": {}}, "origins": {}}
    if method == "turn/start":
        if STATE["turn"] is not None:
            raise ValueError("a turn is already running")
        STATE["body"] = p
        t = Turn(STATE["thread"])
        STATE["turn"], STATE["steer"] = t.id, []
        STATE["stop"].clear()
        name, args, text = turn_input(p.get("input") or [])
        threading.Thread(target=run, args=(t, name, args, text), daemon=True).start()
        return {"turn": {"id": t.id, "status": "inProgress", "items": []}}
    if method == "turn/steer":
        if STATE["turn"] is None or p.get("expectedTurnId") != STATE["turn"]:
            raise ValueError("no active turn matches expectedTurnId")
        STATE["steer"] += [i.get("text", "") for i in p.get("input") or [] if i.get("type") == "text"]
        return {"turnId": STATE["turn"]}
    if method == "turn/interrupt":
        STATE["stop"].set()
        return {}
    if method == "thread/compact/start":
        t = Turn(STATE["thread"])
        STATE["turn"] = t.id
        note("turn/started", threadId=t.tid, turn={"id": t.id, "status": "inProgress", "items": []})
        t.item({"type": "contextCompaction", "id": f"cc-{uuid.uuid4().hex[:8]}"})
        t.end()
        return {}
    if method == "review/start":
        t = Turn(STATE["thread"])
        STATE["turn"] = t.id
        target = p.get("target") or {}
        threading.Thread(target=run, args=(t, "say", "", f"review: {target.get('instructions') or target.get('type')}"),
                         daemon=True).start()  # fmt: skip
        return {"turn": {"id": t.id}}
    raise ValueError(f"the fake does not know {method}")


def main() -> None:
    os.makedirs(HOME, exist_ok=True)
    log(f"START {os.getpid()}")
    for raw in sys.stdin:
        msg = json.loads(raw)
        if "method" not in msg:
            continue  # a reply to our request
        if "id" not in msg:
            continue  # initialized
        try:
            send({"id": msg["id"], "result": handle(msg)})
        except (ValueError, LookupError) as e:
            send({"id": msg["id"], "error": {"code": -32600, "message": str(e)}})
    if os.environ.get("FAKE_CODEX_HANG"):
        while True:
            time.sleep(60)
    drop_lease()


if __name__ == "__main__":
    main()
```

In `tests/conftest.py`, next to `FAKE_OPENCODE` and `fake_opencode`:

```python
FAKE_CODEX = Path(__file__).parent / "fake_codex.py"


@pytest.fixture
def fake_codex(tmp_path: Path) -> str:
    """An executable that runs the fake codex app-server, as a session would."""
    path = tmp_path / "bin-codex" / "codex"
    path.parent.mkdir()
    home = tmp_path / "fake-codex-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_CODEX_HOME="{home}"\n'
        f'export FAKE_CODEX_LOG="{tmp_path / "fake-codex.log"}"\n'
        f'exec "{sys.executable}" "{FAKE_CODEX}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)
```

(`FAKE_OPENCODE` is defined the same way near the top of `conftest.py`; put `FAKE_CODEX` beside it.)

- [ ] **Step 2: Write the failing session tests**

`tests/test_session_codex.py`:

```python
import asyncio
import json
import os
import signal
from pathlib import Path

import pytest

from aegis.meta import MetaStore
from aegis.session import Host, Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store

from .conftest import until


class CX:
    def __init__(self, tmp_path: Path, fake: str):
        self.published: list[tuple[str, list[dict]]] = []
        self.path = tmp_path / "state" / "transcripts" / "log-cx.jsonl"
        self.metas = MetaStore(tmp_path / "state" / "sessions")
        self.tmp_path, self.fake = tmp_path, fake
        self.log = tmp_path / "fake-codex.log"
        self.session = self.make()

    def make(self, **meta) -> Session:
        return Session(
            log_id="log-cx",
            spec=SpawnSpec("cx", "openai/fake-pro", "high", "full", self.tmp_path, harness="codex"),
            handle="quiet-okapi",
            store=Store(self.path),
            stderr_path=self.tmp_path / "state" / "stderr" / "log-cx.log",
            claude_bin="claude-unused",
            codex_bin=self.fake,
            publish=lambda ch, ops: self.published.append((ch, ops)),
            metas=self.metas,
            **meta,
        )

    def prose(self) -> list[str]:
        return [e["md"] for e in self.session.entries() if e["kind"] == "prose"]

    def done_lines(self) -> int:
        return sum(e["summary"].startswith(("done in", "interrupted")) for e in self.session.entries())

    async def turn(self, text: str) -> None:
        before = self.done_lines()
        await self.session.send(text)
        await until(lambda: self.done_lines() > before and self.session.status == "idle",
                    timeout=5, what=f"{text!r}")  # fmt: skip

    def pids(self, tag: str) -> list[int]:
        return [int(ln.split()[1]) for ln in self.log.read_text().splitlines() if ln.startswith(tag + " ")]

    def refold_matches(self) -> bool:
        records, damaged = read_store(self.path)
        return damaged == 0 and fold_records(records).entries() == self.session.entries()

    def patches_rebuild_entries(self) -> bool:
        shown: dict[str, dict] = {}
        for ch, ops in self.published:
            if ch != self.session.channel:
                continue
            for op in ops:
                if "upsert" in op:
                    shown[op["upsert"]["id"]] = op["upsert"]
                else:
                    shown.pop(op["remove"], None)
        return list(shown.values()) == self.session.snapshot()["entries"]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
async def cx(tmp_path, fake_codex):
    h = CX(tmp_path, fake_codex)
    await h.session.start()
    yield h
    await h.session.stop()
    assert h.refold_matches(), "live entries differ from a fold of the store"
    assert h.patches_rebuild_entries(), "the published patches do not add up"


async def test_a_prompt_runs_a_turn_with_context_and_model(cx):
    await cx.turn("hello")
    s = cx.session
    assert cx.prose() == ["you said: hello"]
    assert s.context_tokens == 1030 and s.context_window == 258400
    assert s.model_id == "openai/fake-pro" and s.resume_id
    assert s.wire()["harness_label"] == "Codex"
    assert any(e["summary"] == "Codex 0.162.1 · openai/fake-pro" for e in s.entries())


async def test_text_streams_before_the_turn_ends(cx):
    await cx.session.send("/stream 4")
    await until(lambda: any("chunk1" in m for m in cx.prose()), what="the first chunk")
    assert not any("chunk4" in m for m in cx.prose()), "drawn while it streams"
    await until(lambda: cx.done_lines() == 1, timeout=5, what="the end")


async def test_no_delta_line_is_stored(cx):
    await cx.turn("/stream 3")
    stored = [json.loads(r["line"])["method"] for r in read_store(cx.path)[0] if r.get("src") == "codex"]
    assert stored and not any(m.endswith("/delta") or "Delta" in m for m in stored)


async def test_interrupt_ends_the_turn_and_marks_the_call(cx):
    await cx.session.send("/sleep 5")
    await until(lambda: any(e["kind"] == "tool" for e in cx.session.entries()), what="the call")
    await cx.session.interrupt()
    await until(lambda: cx.session.status == "idle", timeout=5, what="idle")
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "err"


async def test_stop_and_resume_keep_the_conversation(cx):
    await cx.turn("remember PELICAN")
    sid = cx.session.resume_id
    await cx.session.stop()
    await cx.turn("/recall")
    assert cx.session.resume_id == sid and "PELICAN" in cx.prose()[-1]


async def test_a_resume_codex_lost_starts_anew_and_says_so(cx):
    await cx.turn("first")
    await cx.session.stop()
    cx.session.resume_id = "01a10000-0000-7000-8000-000000000000"
    await cx.turn("second")
    assert cx.session.resume_id != "01a10000-0000-7000-8000-000000000000"
    assert any("no longer had this conversation" in e["summary"] for e in cx.session.entries())


async def test_stop_leaves_nothing_the_child_started(cx):
    await cx.turn("hello")
    pids = cx.pids("START") + cx.pids("LEASE")
    await cx.session.stop()
    await until(lambda: not any(alive(p) for p in pids), timeout=8, what="every child gone")


async def test_a_child_that_ignores_stdin_is_ended_by_its_group(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_HANG", "1")
    h = CX(tmp_path, fake_codex)
    await h.session.start()
    await h.turn("hello")
    pids = h.pids("START") + h.pids("LEASE")
    await h.session.stop()
    await until(lambda: not any(alive(p) for p in pids), timeout=15, what="the group gone")


async def test_an_exit_kills_what_the_child_left_and_the_resume_works(cx):
    await cx.turn("remember OKAPI")
    lease = cx.pids("LEASE")[-1]
    await cx.session.send("/exit 3")
    await until(lambda: cx.session.status == "stopped", timeout=5, what="stopped")
    await until(lambda: not alive(lease), timeout=5, what="the lease holder gone")
    await cx.turn("/recall")
    assert "OKAPI" in cx.prose()[-1]


class TokenHost(Host):
    def spawn_args(self, session):
        return ("http://127.0.0.1:9/mcp", "tok-secret"), "primer"


async def test_the_token_reaches_the_child_only_by_its_environment(tmp_path, fake_codex):
    h = CX(tmp_path, fake_codex)
    h.session = h.make(host=TokenHost())
    await h.session.start()
    await h.turn("/argv")
    assert "tok-secret" not in h.prose()[-1] and "AEGIS_SESSION_TOKEN" in h.prose()[-1]
    await h.turn("/env")
    assert h.prose()[-1] == "env: tok-secret"
    await h.session.stop()


async def test_a_request_from_codex_is_refused_and_the_turn_goes_on(cx):
    await cx.turn("/ask")
    assert cx.prose()[-1] == "asked"


async def test_a_line_over_64_kb_reaches_the_store(cx):
    await cx.turn("/big 200")
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "ok" and cx.prose()[-1] == "big done"


async def test_a_subagent_shows_as_a_task_with_steps(cx):
    await cx.turn("/spawn")
    (t,) = [e for e in cx.session.entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0
    assert cx.session.context_tokens == 1030  # the child's usage is not the context


async def test_an_exit_leaves_the_session_stopped(cx):
    await cx.session.send("/exit 1")
    await until(lambda: cx.session.status == "stopped", timeout=5, what="stopped")
    assert any("Codex exited with code 1" in e["summary"] for e in cx.session.entries())
```

Add `Host` to the test file's imports: `from aegis.session import Host, Session, SpawnSpec`. `Session(host=...)` defaults to the shared `NO_HOST`, whose `spawn_args` returns no MCP; `TokenHost` overrides only that.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_session_codex.py -q`
Expected: FAIL: `Session.__init__() got an unexpected keyword argument 'codex_bin'`.

- [ ] **Step 4: Write the process**

`src/aegis/codex/process.py`:

```python
"""One ``codex app-server`` child per session, driven over JSON-RPC on stdio.

Measured on codex-cli 0.162.1 (spec ``2026-10-09-aegis-2-codex-harness-design.md``):

- Model, effort and sandbox travel with every ``turn/start``, so a change
  needs no restart; only a new provider does, because a thread names its
  provider when it opens.
- A prompt sent mid-turn is ``turn/steer``, read at the next model request.
  It names the running turn, and Codex refuses it once that turn has ended,
  so a refused steer starts a new turn instead.
- The child holds a writer lease on its thread. Closing stdin ends it
  cleanly; a child that died left a grandchild (the plugin clone) holding the
  lease, so after any exit the whole process group is killed.
- An ``item/completed`` carries a command's whole output on one line, far
  past asyncio's 64 KB ``readline`` default.
- No line names the Codex version or the model of a turn, so the process
  writes ``aegis/*`` lines for them through ``on_line`` (``stream.OWN``).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from ..claude.control import Catalog
from ..harness import Launch
from .config import (
    SANDBOX_MODE,
    SANDBOX_POLICY,
    argv,
    catalog_from,
    child_env,
    provider_model,
    split_model,
)
from .stream import DELTAS, STORED

START_S = 15.0
REQUEST_S = 30.0
TERM_GRACE_S = 5.0
STDERR_TAIL = 20
LINE_LIMIT = 64 * 1024 * 1024
LOST = "no rollout found"


class RpcError(Exception):
    """app-server answered a request with an error."""


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _aegis_version() -> str:
    try:
        from importlib.metadata import version

        return version("aegis-harness")
    except Exception:  # noqa: BLE001 — an unknown version must not block a start
        return "0"


class CodexProcess:
    def __init__(self, bin: str, launch: Launch) -> None:
        self._bin = bin
        self._launch = launch
        self.model, self.effort, self.permission = (
            launch.model,
            launch.effort,
            launch.permission,
        )
        self._provider = split_model(launch.model)[0]
        self._thread: str | None = None
        self._turn: str | None = None
        self._ended: deque[str] = deque(maxlen=16)
        self._proc: asyncio.subprocess.Process | None = None
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wait_task: asyncio.Task | None = None
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL)
        self._catalog: Catalog | None = None
        self._skills: dict[str, str] = {}
        self._restart = False
        self._quiet = False

    # -- what the session reads --------------------------------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def session_id(self) -> str | None:
        return self._thread

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        await self._spawn()
        try:
            await self._open(self._launch.resume_id)
        except (RpcError, OSError, TimeoutError, KeyError, TypeError) as e:
            await self._end_child()
            raise ConnectionError(f"codex app-server did not open a thread: {e}") from e
        await self._try_catalog()

    async def _spawn(self) -> None:
        self._quiet = False
        mcp = self._launch.mcp
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            *argv(self._bin, mcp[0] if mcp else None),
            cwd=self._launch.cwd,
            env=child_env(os.environ, mcp),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
            start_new_session=True,
        )
        self._keep(self._drain(self._proc.stderr))
        self._keep(self._read())
        self._wait_task = asyncio.create_task(self._wait())
        try:
            init = await self._call(
                "initialize",
                {"clientInfo": {"name": "aegis", "version": _aegis_version()}},
                START_S,
            )
        except (RpcError, OSError, TimeoutError) as e:
            await self._end_child()
            tail = "; ".join(self._tail) or "no output"
            raise ConnectionError(f"codex app-server did not start: {e}: {tail}") from e
        self._write({"method": "initialized"})
        self._emit("aegis/initialize", init)

    async def _open(self, resume: str | None) -> None:
        provider, model_id = split_model(self.model)
        params: dict[str, Any] = {
            "cwd": str(self._launch.cwd),
            "approvalPolicy": "never",
            "sandbox": SANDBOX_MODE[self.permission],
        }
        if model_id:
            params |= {"model": model_id, "modelProvider": provider}
        if self._launch.system_prompt:
            params["developerInstructions"] = self._launch.system_prompt
        result: dict | None = None
        if resume:
            try:
                result = await self._call("thread/resume", {"threadId": resume, **params})
            except RpcError as e:
                if LOST not in str(e):
                    raise
        if result is None:
            result = await self._call("thread/start", params)
        self._thread = str(_dict(result.get("thread"))["id"])
        self._provider = provider
        self._emit("aegis/thread", result)

    def _keep(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _note(self, line: str) -> None:
        if not line:
            return
        self._tail.append(line)
        with self._launch.stderr_path.open("a") as f:
            f.write(line + "\n")

    async def _drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while raw := await stream.readline():
            self._note(raw.decode(errors="replace").rstrip())

    async def _wait(self) -> None:
        assert self._proc is not None
        proc = self._proc
        code = await proc.wait()
        self._signal(proc, signal.SIGKILL)  # whatever it left holds the thread
        self._fail_pending(ConnectionResetError("codex app-server exited"))
        if self._quiet:
            return
        self._quiet = True
        await asyncio.sleep(0.05)  # let the stderr drain catch up
        self._launch.on_exit(code, list(self._tail))

    async def terminate(self) -> None:
        await self._end_child()

    async def _end_child(self) -> None:
        self._quiet = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                proc.stdin.close()  # a clean exit releases the thread's writer lease
            try:
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            except TimeoutError:
                self._signal(proc, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
                except TimeoutError:
                    self._signal(proc, signal.SIGKILL)
                    await proc.wait()
        if proc is not None:
            self._signal(proc, signal.SIGKILL)
        for t in list(self._tasks):
            t.cancel()
        if self._wait_task is not None:
            self._wait_task.cancel()
        self._fail_pending(ConnectionResetError("codex app-server was stopped"))

    @staticmethod
    def _signal(proc: asyncio.subprocess.Process, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _fail_pending(self, err: Exception) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(err)

    # -- the wire -----------------------------------------------------
    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while raw := await stdout.readline():
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                msg: Any = json.loads(line)
            except ValueError:
                self._note(f"not JSON on stdout: {line[:200]}")
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and "method" in msg:
                self._refuse(msg)
            elif "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            else:
                self._deliver(str(msg.get("method") or ""), _dict(msg.get("params")), line)

    def _refuse(self, msg: dict) -> None:
        """aegis has no approval prompt, and a request nobody answers would
        hang the turn, so every request from Codex is refused at once."""
        self._note(f"refused a request from codex: {msg.get('method')}")
        self._write(
            {"id": msg["id"], "error": {"code": -32601, "message": "aegis answers no requests"}}
        )

    def _deliver(self, method: str, p: dict, line: str) -> None:
        if p.get("threadId") == self._thread:
            if method == "turn/started":
                self._turn = str(_dict(p.get("turn")).get("id") or "") or None
            elif method == "turn/completed":
                self._ended.append(str(_dict(p.get("turn")).get("id") or ""))
                self._turn = None
        if method in STORED or method in DELTAS:
            self._launch.on_line(line)

    def _emit(self, method: str, params: dict) -> None:
        self._launch.on_line(json.dumps({"method": method, "params": params}))

    def _write(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ConnectionResetError("codex app-server is not running")
        try:
            self._proc.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            raise ConnectionResetError(str(e)) from e

    async def _call(self, method: str, params: dict, timeout: float = REQUEST_S) -> dict:
        if not self.running:
            raise ConnectionResetError("codex app-server is not running")
        self._next += 1
        rid = self._next
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self._write({"id": rid, "method": method, "params": params})
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"]
            raise RpcError(str(_dict(err).get("message") or err))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    # -- turns --------------------------------------------------------
    async def send(self, text: str) -> None:
        await self._maybe_restart()
        if self._catalog is None:
            await self._try_catalog()
        if text.startswith("/"):
            await self._command(text)
            return
        items = [{"type": "text", "text": text, "text_elements": []}]
        if self._turn is not None:
            try:
                await self._call(
                    "turn/steer",
                    {"threadId": self._thread, "expectedTurnId": self._turn, "input": items},
                )
                return
            except RpcError:
                pass  # the turn ended between the check and the call
        await self._start_turn(items)

    async def _start_turn(self, items: list[dict]) -> None:
        _, model_id = split_model(self.model)
        self._emit(
            "aegis/turn",
            {"model": self.model, "effort": self.effort, "permission": self.permission},
        )
        params: dict[str, Any] = {
            "threadId": self._thread,
            "input": items,
            "sandboxPolicy": SANDBOX_POLICY[self.permission],
        }
        if model_id:
            params["model"] = model_id
        if effort := self._effort():
            params["effort"] = effort
        result = await self._call("turn/start", params)
        tid = str(_dict(result.get("turn")).get("id") or "")
        if tid and tid not in self._ended:
            self._turn = tid

    def _effort(self) -> str | None:
        m = self._catalog.model(self.model) if self._catalog else None
        return self.effort if m is not None and self.effort in m.efforts else None

    async def _command(self, text: str) -> None:
        name, _, args = text[1:].partition(" ")
        args = args.strip()
        if name == "compact":
            self._emit("aegis/command", {"line": text, "kind": "compact"})
            await self._call("thread/compact/start", {"threadId": self._thread})
            return
        if name == "review":
            target = {"type": "custom", "instructions": args} if args else {"type": "uncommittedChanges"}
            self._emit("aegis/command", {"line": text, "kind": "review"})
            await self._call("review/start", {"threadId": self._thread, "target": target})
            return
        path = self._skills.get(name)
        if path is None:
            raise RpcError(f"/{name} is not a Codex skill in {self._launch.cwd}")
        self._emit("aegis/command", {"line": text, "kind": "skill"})
        items: list[dict] = [{"type": "skill", "name": name, "path": path}]
        if args:
            items.append({"type": "text", "text": args, "text_elements": []})
        await self._start_turn(items)

    async def interrupt(self) -> None:
        if self._turn is None:
            return
        await self._call("turn/interrupt", {"threadId": self._thread, "turnId": self._turn})

    async def set(self, kind: str, value: str) -> None:
        if kind == "model":
            if split_model(value)[0] != self._provider:
                self._restart = True
            self.model = value
        elif kind == "effort":
            self.effort = value
        elif kind == "permission":
            self.permission = value

    async def _maybe_restart(self) -> None:
        """A new provider: the thread reopens under it, in a new child, once no
        turn runs."""
        if not self._restart or self._turn is not None:
            return
        self._restart = False
        await self._end_child()
        try:
            await self._spawn()
            await self._open(self._thread)
        except (RpcError, OSError, TimeoutError) as e:
            # _end_child silenced the old child's exit, so this one says it.
            self._launch.on_exit(-1, [*self._tail, f"the restart failed: {e}"])
            raise ConnectionResetError(f"codex app-server did not restart: {e}") from e

    # -- the catalog ----------------------------------------------------
    async def _try_catalog(self) -> None:
        try:
            self._catalog = await self._read_catalog()
        except (RpcError, OSError, TimeoutError):
            self._catalog = None

    async def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = await self._read_catalog()
        return self._catalog

    async def _read_catalog(self) -> Catalog:
        models = (await self._call("model/list", {})).get("data") or []
        skills: list[dict] = []
        found = await self._call("skills/list", {"cwds": [str(self._launch.cwd)]})
        for group in found.get("data") or []:
            skills += [s for s in _dict(group).get("skills") or [] if isinstance(s, dict)]
        self._skills = {str(s["name"]): str(s.get("path") or "") for s in skills if s.get("name")}
        provider = split_model(self.model)[0]
        listed = await self._provider_models(provider) if provider not in ("", "openai") else []
        return catalog_from(models, skills, listed, self.model)

    async def _provider_models(self, provider: str) -> list:
        """An OpenAI-compatible provider's own ``/models``, so ``/model`` can
        offer its models. Nothing on any failure: the catalog still has the
        session's model."""
        try:
            cfg = await self._call("config/read", {})
            providers = _dict(_dict(cfg.get("config")).get("model_providers"))
            base = _dict(providers.get(provider)).get("base_url")
            if not isinstance(base, str):
                return []
            async with httpx.AsyncClient() as client:
                r = await asyncio.wait_for(client.get(base.rstrip("/") + "/models"), 10)
            r.raise_for_status()
            data = r.json().get("data") or []
        except (RpcError, httpx.HTTPError, OSError, TimeoutError, ValueError, AttributeError):
            return []
        return [m for d in data if isinstance(d, dict) and (m := provider_model(provider, d))]


async def probe(bin: str, cwd: Path, stderr_path: Path, model: str = "") -> Catalog:
    """A catalog for a cwd with no live process: start ``codex app-server``,
    ask, end it. About half a second and no tokens."""
    p = CodexProcess(
        bin,
        Launch(
            cwd=cwd, model=model, effort="", permission="read", resume_id=None, mcp=None,
            system_prompt=None, stderr_path=stderr_path,
            on_line=lambda line: None, on_exit=lambda code, tail: None,
        ),
    )  # fmt: skip
    await p._spawn()
    try:
        return await p._read_catalog()
    except RpcError as e:
        raise ConnectionError(f"codex app-server gave no catalog: {e}") from e
    finally:
        await p.terminate()
```

`src/aegis/codex/harness.py`:

```python
"""Codex behind the harness interface: ``codex app-server`` over stdio."""

from __future__ import annotations

from pathlib import Path

from ..claude.control import Catalog
from ..harness import Launch
from .process import CodexProcess, probe
from .stream import LABEL


class Codex:
    name = "codex"
    src = "codex"
    label = LABEL
    tool_prefix = "mcp__aegis__"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> CodexProcess:
        return CodexProcess(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await probe(self.bin, spec.cwd, stderr_path, spec.model)
```

- [ ] **Step 5: Name the harness everywhere a harness is named**

Every change below copies the `opencode` line beside it.

`src/aegis/harness.py`: the docstring's first line becomes "A harness is the agent CLI a session runs: Claude Code, OpenCode or Codex."; `harness_for` becomes:

```python
def harness_for(
    name: str, claude_bin: str, opencode_bin: str, codex_bin: str = "codex"
) -> Harness:
    if name == "claude-code":
        from .claude.harness import ClaudeCode

        return ClaudeCode(claude_bin)
    if name == "opencode":
        from .opencode.harness import OpenCode

        return OpenCode(opencode_bin)
    if name == "codex":
        from .codex.harness import Codex

        return Codex(codex_bin)
    raise ValueError(f"no harness named {name!r}")
```

`src/aegis/agents.py`:

```python
HARNESSES = ("claude-code", "opencode", "codex")
```

```python
CODEX_MODEL = "a Codex model is provider/model, such as openai/gpt-5.5"


def _model_error(harness: str, model: str) -> str | None:
    if harness == "opencode" and "/" not in model:
        return OPENCODE_MODEL
    if harness == "codex" and "/" not in model:
        return CODEX_MODEL
    return None
```

`src/aegis/session.py`: add `codex_bin: str = "codex",` after `opencode_bin: str = "opencode",` in `Session.__init__`, and pass it: `self.harness = harness_for(spec.harness, claude_bin, opencode_bin, codex_bin)`.

`src/aegis/registry.py`: line 46 becomes `harness = harness_for(meta.get("harness") or "claude-code", "claude", "opencode", "codex")`; add `codex_bin: str = "codex",` after `opencode_bin` in `__init__`, `self._codex_bin = codex_bin` after `self._opencode_bin = opencode_bin`, and `codex_bin=self._codex_bin,` after `opencode_bin=self._opencode_bin,` where sessions are built (line 192).

`src/aegis/app.py`: add `codex_bin: str = "codex",` after `opencode_bin` in `App.__init__`, `self.codex_bin = codex_bin` after `self.opencode_bin = opencode_bin`, `codex_bin=codex_bin,` after `opencode_bin=opencode_bin,` (line 197), and `_bin` becomes:

```python
    def _bin(self, harness: str) -> str:
        return {"opencode": self.opencode_bin, "codex": self.codex_bin}.get(
            harness, self.claude_bin
        )
```

`src/aegis/config_ops.py:36`:

```python
        return {"claude-code": app.claude_bin, "opencode": app.opencode_bin, "codex": app.codex_bin}
```

`src/aegis/doctor.py`: `LABELS = {"claude-code": "Claude Code", "opencode": "OpenCode", "codex": "Codex"}`, and line 84 becomes `h = harness_for(name, bins["claude-code"], bins["opencode"], bins["codex"])`.

`src/aegis/cli.py`:
- the bare-`aegis` call to `serve(...)` (line 62): add `codex="codex",` after `opencode="opencode",`;
- `_detach(...)` (line 134): add a parameter `codex: str = "codex",` after `opencode`, and `"--codex", codex,` after `"--opencode", opencode,` in `cmd`;
- `serve`: add `codex: str = typer.Option("codex", help="The codex executable to run."),` after the `opencode` option; pass `codex` to `_detach` (line 290: `roots, host, local, port, claude, log_level, origin, urls, opencode, name, codex` and match the parameter order you gave `_detach`), and `codex_bin=codex,` to `App(...)` after `opencode_bin=opencode,`;
- `_bins` becomes `def _bins(claude: str, opencode: str, codex: str) -> dict[str, str]: return {"claude-code": claude, "opencode": opencode, "codex": codex}`;
- `doctor` and `init`: a `codex` option like their `opencode` option ("The codex executable to check." / "The codex executable to look for."), and `_bins(claude, opencode, codex)`.

Then find anything the grep in the spec work missed:

```bash
grep -rn -E 'harness_for\(|_bins\(|opencode_bin' src/aegis --include=*.py | grep -v -E 'codex|^src/aegis/opencode/'
```

Expected: every remaining line already passes or needs no codex argument (default `"codex"`). Fix any that builds a bins dict without `"codex"`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_session_codex.py tests/test_codex_stream.py tests/test_codex_config.py tests/test_session_opencode.py tests/test_agents.py tests/test_doctor.py -q`
Expected: PASS. If `tests/test_doctor.py` asserts the exact list of harnesses `detect` finds, add `codex` to its expectation; `init` proposes no Codex agent (the spec does not ask for one).

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(codex): a codex app-server child per session behind the harness interface" -- tests/fake_codex.py tests/conftest.py src/aegis/codex/process.py src/aegis/codex/harness.py src/aegis/harness.py src/aegis/agents.py src/aegis/session.py src/aegis/registry.py src/aegis/app.py src/aegis/config_ops.py src/aegis/cli.py src/aegis/doctor.py tests/test_session_codex.py
```

(Add any test file Step 6 made you change to the path list.)

---

### Task 5: Steer, settings and commands

**Files:**
- Modify: `tests/test_session_codex.py` (append)

**Interfaces:**
- Consumes: Task 4's `CodexProcess.send`, `set`, `_command`, `_start_turn`, `_maybe_restart`; the fake's `body`, `sleep`, `recall` scripts.
- Produces: tests only; the code is Task 4's. A failure here is fixed in `src/aegis/codex/process.py`.

- [ ] **Step 1: Write the tests**

Append to `tests/test_session_codex.py`:

```python
async def test_a_prompt_sent_mid_turn_is_steered_into_the_same_turn(cx):
    await cx.session.send("/sleep 0.6")
    await until(lambda: any(e["kind"] == "tool" for e in cx.session.entries()), what="the call")
    await cx.session.send("steer this")
    await until(lambda: cx.done_lines() == 1 and cx.session.status == "idle", timeout=5, what="idle")
    assert [e["status"] for e in cx.session.entries() if e["kind"] == "user"] == ["ok", "ok"]
    assert "also: steer this" in cx.prose()[-1]


async def test_a_steer_that_meets_an_ended_turn_starts_a_new_one(cx):
    await cx.turn("first")
    cx.session._proc._turn = "a-turn-that-ended"  # the race: our id is stale
    await cx.turn("second")
    assert cx.prose()[-1] == "you said: second"


async def test_model_effort_and_permission_apply_from_the_next_turn_without_a_restart(cx):
    pid = cx.session.pid
    await cx.session.configure(model="openai/fake-flash", permission="read")
    await cx.turn("/body")
    body = json.loads(cx.prose()[-1].removeprefix("body: "))
    assert body["model"] == "fake-flash" and "effort" not in body  # fake-flash has no efforts
    assert body["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    await cx.session.configure(model="openai/fake-pro", effort="low")
    await cx.turn("/body")
    body = json.loads(cx.prose()[-1].removeprefix("body: "))
    assert body["model"] == "fake-pro" and body["effort"] == "low"
    assert cx.session.pid == pid


async def test_a_new_provider_restarts_the_child_on_the_same_thread(cx):
    await cx.turn("remember HERON")
    sid, pid = cx.session.resume_id, cx.session.pid
    await cx.session.configure(model="other/fake-x")
    await cx.turn("/recall")
    assert cx.session.pid != pid and cx.session.resume_id == sid
    assert "HERON" in cx.prose()[-1]


async def test_a_skill_shows_as_typed_and_runs(cx):
    await cx.turn("/bash echo hi => hi")
    (u,) = [e for e in cx.session.entries() if e["kind"] == "user"]
    assert u["md"] == "/bash echo hi => hi"
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "ok"


async def test_compact_and_review_are_codex_commands(cx):
    await cx.turn("hello")
    await cx.turn("/compact")
    await cx.turn("/review the README")
    users = [e["md"] for e in cx.session.entries() if e["kind"] == "user"]
    assert users == ["hello", "/compact", "/review the README"]
    assert cx.prose()[-1] == "you said: review: the README"


async def test_an_unknown_command_fails_the_send_and_ends_the_turn(cx):
    with pytest.raises(Exception):
        await cx.session.send("/nosuch")
    await until(lambda: cx.session.status == "idle", timeout=5, what="idle")


async def test_the_catalog_has_the_fakes_models_skills_and_codex_commands(cx):
    cat = await cx.session.catalog_task
    assert [m.value for m in cat.models][:2] == ["openai/fake-pro", "openai/fake-flash"]
    names = [c["name"] for c in cat.commands]
    assert names[:2] == ["compact", "review"] and "sleep" in names
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_session_codex.py -q`
Expected: PASS. Two places may need a fix in `process.py`:
- `test_compact_and_review_are_codex_commands`: the fake's review turn says "you said: review: the README" because it runs the `say` script; if the session never leaves `working` after `/compact`, check that the fake's compact turn emits `turn/started` before `item/*` (it does) and that the parser's `_turn_completed` saw `turn_open`.
- `test_an_unknown_command_fails_the_send_and_ends_the_turn`: `Session.send` records a `harness_error` and moves to idle on any exception (`session.py:553-556`), so no process change should be needed.

- [ ] **Step 3: Commit**

```bash
git commit -m "test(codex): steer, per-turn settings, provider restart and commands" -- tests/test_session_codex.py
```

(Add `src/aegis/codex/process.py` to the paths if Step 2 changed it.)

---

### Task 6: Prices, the card's cost and the usage report

**Files:**
- Modify: `src/aegis/usage/prices.py`
- Modify: `src/aegis/codex/stream.py` (`Parser._price`)
- Modify: `src/aegis/usage/scan.py:100-115,317-331` and a new `_codex_request`
- Modify: `src/aegis/usage/store.py:64,88` (`codex` lines parsed)
- Modify: `tests/test_codex_stream.py`, `tests/test_session_codex.py` (append)
- Test: the existing usage tests (`ls tests | grep -i usage`)

**Interfaces:**
- Consumes: Task 2's `Parser` (`self.model`, `_price(u)`), `Usage`.
- Produces: `aegis.usage.prices.OPENAI: dict[str, Prices]`; `codex_prices_for(model: str | None) -> Prices | None` taking an aegis Codex model (`openai/gpt-5.5`, `openrouter/x:free`).

- [ ] **Step 1: Look the prices up**

OpenAI's prices change (a 50% cut on 2026-07-30 per secondary sources). Fetch the vendor's page, never a memory:

```bash
curl -s https://api.firecrawl.dev/v2/scrape -H "Authorization: Bearer $(cat /home/apiad/Workspace/.claude/firecrawl.token)" \
  -H 'Content-Type: application/json' -d '{"url":"https://openai.com/api/pricing/","formats":["markdown"]}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['markdown'])" | grep -i -E -A3 'gpt-(5\.5|5\.6|6)'
```

Write down input, cached input and output per million tokens for each model `model/list` returned (`gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`). Secondary sources gave, on 2026-10-09: `gpt-6.1-sol` 2 / 0.10 / 10, `gpt-6-sol` 2 / 0.20 / 10, `gpt-6-luna` 0.10 / 0.01 / 0.50, `gpt-5.6-terra` 2 / 0.20 / 12. Use the page's numbers where they differ, and leave out any model the page does not price: an unpriced model is counted as unpriced, never charged another's rate.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_codex_stream.py`:

```python
def test_a_priced_model_costs_its_requests_and_a_free_one_costs_nothing():
    from aegis.usage.prices import codex_prices_for

    assert codex_prices_for("openrouter/nvidia/x:free").cost(inp=10**6, out=10**6, cc5=0, cc1=0, cache_read=0) == 0
    assert codex_prices_for("openrouter/paid/x") is None
    assert codex_prices_for("ollama/qwen") is None
    p = Parser()
    p.feed(line("aegis/thread", thread={"id": "t1", "model": "gpt-6-sol", "modelProvider": "openai"}))
    p.feed(line("turn/started", threadId="t1", turn={"id": "u1"}))
    last = {"inputTokens": 1_000_000, "cachedInputTokens": 0, "outputTokens": 0, "totalTokens": 1_000_000}
    p.feed(line("thread/tokenUsage/updated", threadId="t1", turnId="u1", tokenUsage={"last": last, "total": last}))
    (r,) = p.feed(line("turn/completed", threadId="t1", turn={"id": "u1", "status": "completed"}))
    assert r.cost_usd == pytest.approx(float(codex_prices_for("openai/gpt-6-sol").input))
```

(If OpenAI's page no longer prices `gpt-6-sol`, use any model it does price, in both places.)

Append to `tests/test_session_codex.py`:

```python
async def test_a_free_model_shows_zero_cost(tmp_path, fake_codex):
    h = CX(tmp_path, fake_codex)
    h.session.spec = h.session.spec.__class__(
        "cx", "openrouter/fake:free", "", "full", tmp_path, harness="codex"
    )
    await h.session.start()
    await h.turn("hello")
    assert h.session.cost_usd == 0
    await h.session.stop()
```

Find the usage scanner's test file and add a Codex store case modelled on its OpenCode case:

```bash
grep -ln "opencode" tests/test_usage*.py
```

In that file, copy the test that writes an OpenCode store and checks unpriced tokens; make a Codex version whose store holds an `aegis/turn` line (`{"method":"aegis/turn","params":{"model":"openai/gpt-6-sol",...}}`) and one `thread/tokenUsage/updated` line, and assert the session's cost equals the token count times `codex_prices_for("openai/gpt-6-sol")`'s input rate, and that a second identical usage line with the same `turnId` and index is not counted twice.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_codex_stream.py -q -k priced`
Expected: FAIL: `cannot import name 'codex_prices_for'`.

- [ ] **Step 4: Add the prices**

In `src/aegis/usage/prices.py`, extend the module docstring with one paragraph:

```
OpenAI's rates are copied from https://openai.com/api/pricing/ on the day in
``OPENAI_AS_OF``. OpenAI charges no cache write, so a written token is priced
as input. A Codex model is priced only when it is ``openai/<id>`` with an id
here, or any provider's ``:free`` model (OpenRouter's suffix for a model that
costs nothing); everything else is unpriced.
```

and append (fill the table from Step 1; these four rows are the secondary-source values to check):

```python
OPENAI_AS_OF = "2026-10-09"


def _o(inp: str, cached: str, out: str) -> Prices:
    return Prices(Decimal(inp), Decimal(out), Decimal(inp), Decimal(inp), Decimal(cached))


OPENAI: dict[str, Prices] = {
    "gpt-6.1-sol": _o("2", "0.10", "10"),
    "gpt-6-sol": _o("2", "0.20", "10"),
    "gpt-6-luna": _o("0.10", "0.01", "0.50"),
    "gpt-5.6-terra": _o("2", "0.20", "12"),
}


def codex_prices_for(model: str | None) -> Prices | None:
    """The rates for a Codex model (``provider/model``), or None."""
    if not model:
        return None
    provider, _, model_id = model.partition("/")
    if model_id.endswith(":free"):
        return _ZERO
    return OPENAI.get(model_id) if provider == "openai" else None
```

- [ ] **Step 5: Price requests in the parser**

In `src/aegis/codex/stream.py`, add `from ..usage.prices import codex_prices_for` to the imports and replace `_price`:

```python
    def _price(self, u: Usage) -> float | None:
        """What one request cost at the turn's model's rates, or None."""
        prices = codex_prices_for(self.model)
        if prices is None:
            return None
        return float(
            prices.cost(
                inp=u.input,
                out=u.output,
                cc5=u.cache_creation,
                cc1=0,
                cache_read=u.cache_read,
            )
        )
```

- [ ] **Step 6: Count Codex requests in the usage report**

`src/aegis/usage/store.py`: in `Line`'s comment and in `lines`, the parsed sources become `("claude", "opencode", "codex")`:

```python
            if src in ("claude", "opencode", "codex"):
```

`src/aegis/usage/scan.py`:
- the module docstring's last paragraph gains: "A Codex session's requests, from its ``thread/tokenUsage/updated`` lines, are priced by ``codex_prices_for`` with the model of their turn's ``aegis/turn`` line.";
- `_cost_of` prices Codex too:

```python
    if provider == DEFAULT_PROVIDER:
        prices = prices_for(model)
    elif provider == "codex":
        prices = codex_prices_for(model)
    else:
        prices = None
```

  (with `from .prices import codex_prices_for, prices_for`);
- in `scan_store`, the source filter becomes `if line.src not in ("claude", "opencode", "codex"): continue`, and before the OpenCode branch:

```python
                if line.src == "codex":
                    model = self._codex_request(scan, line.ts or "", obj, model, key)
                    continue
```

- and the method:

```python
    def _codex_request(self, scan: SessionScan, ts: str, obj: dict, model: str, key: str) -> str:
        """Codex reports each request's tokens in ``thread/tokenUsage/updated``;
        the model is the one its turn was sent with (``aegis/turn``). Returns
        the model in force after this line."""
        params = obj.get("params") or {}
        if obj.get("method") == "aegis/turn":
            return str(params.get("model") or model)
        if obj.get("method") != "thread/tokenUsage/updated":
            return model
        last = (params.get("tokenUsage") or {}).get("last")
        if not isinstance(last, dict):
            return model
        n = self._codex_n.get(key, 0) + 1
        self._codex_n[key] = n
        mark = f"codex:{key}:{params.get('threadId')}:{params.get('turnId')}:{n}"
        if mark in self.seen:
            return model
        self.seen.add(mark)
        cached = int(last.get("cachedInputTokens") or 0)
        write = int(last.get("cacheWriteInputTokens") or 0)
        self._add(
            scan,
            ts,
            model,
            inp=max(0, int(last.get("inputTokens") or 0) - cached - write),
            out=int(last.get("outputTokens") or 0),
            cc5=write,
            cc1=0,
            cache_read=cached,
        )
        return model
```

  with `self._codex_n: dict[str, int] = {}` added in `Scanner.__init__` next to `self.seen`. The model for a Codex session starts as `DEFAULT_MODEL` from the loop; set it to `""` for a Codex store so an unpriced request never borrows Claude's default (`model = "" if stored.harness == "codex" else DEFAULT_MODEL`).

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_codex_stream.py tests/test_session_codex.py $(ls tests/test_usage*.py) -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(codex): price Codex requests on the card and in aegis usage" -- src/aegis/usage/prices.py src/aegis/codex/stream.py src/aegis/usage/scan.py src/aegis/usage/store.py tests/test_codex_stream.py tests/test_session_codex.py $(git diff --name-only -- tests/test_usage*.py)
```

---

### Task 7: The ChatGPT quota gauge

This task needs one real `codex login` with a ChatGPT account (a free one works), because no probe has seen a ChatGPT answer yet. If Alex has not logged in, stop after Step 1 and report that this task is blocked on it; Tasks 8 and 9 do not depend on it.

**Files:**
- Create: `src/aegis/quota/codex.py`
- Create: `tests/fixtures/codex/rate_limits.json`, `tests/fixtures/codex/auth-chatgpt.json`
- Modify: `src/aegis/quota/__init__.py:41,47,196-212`
- Modify: `src/aegis/registry.py:93`
- Create: `tests/test_quota_codex.py`

**Interfaces:**
- Consumes: `aegis.quota.core.QuotaProvider`, `QuotaSnapshot`, `QuotaWindow`, `QuotaError`, `boot_clock`, `_severity`.
- Produces: `aegis.quota.codex.PROVIDER` (`name="codex"`, `label="ChatGPT"`, `harness="codex"`); `read_account(path: Path | None = None) -> str | None`; `parse_limits(result: dict, *, now: float) -> QuotaSnapshot`; `fetch_limits(token: str) -> QuotaSnapshot`; `Quota.turn_ended(harness: str = "claude-code")`.

- [ ] **Step 1: Record a real answer**

Ask Alex to run `codex login --device-auth` (or `codex login`) once. Then record, with secrets removed:

```bash
python3 - <<'EOF'
import json, os, subprocess
home = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
auth = json.load(open(os.path.join(home, "auth.json")))
def scrub(v):
    if isinstance(v, dict): return {k: scrub(x) for k, x in v.items()}
    return "REDACTED" if isinstance(v, str) and len(v) > 40 else v
json.dump(scrub(auth), open("tests/fixtures/codex/auth-chatgpt.json", "w"), indent=1)
p = subprocess.Popen(["codex", "app-server", "--disable", "plugins", "--disable", "remote_plugin"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
w = lambda o: (p.stdin.write(json.dumps(o) + "\n"), p.stdin.flush())
w({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "aegis", "version": "rec"}}})
w({"method": "initialized"})
w({"id": 2, "method": "account/rateLimits/read", "params": {}})
for line in p.stdout:
    m = json.loads(line)
    if m.get("id") == 2:
        r = m.get("result") or m
        r.pop("accountId", None)
        json.dump(r, open("tests/fixtures/codex/rate_limits.json", "w"), indent=1)
        break
p.stdin.close(); p.wait(10)
EOF
cat tests/fixtures/codex/rate_limits.json | head -40
```

Expected: `rateLimits.primary` and `rateLimits.secondary` with `usedPercent`, `windowDurationMins` and `resetsAt`. Read the auth fixture's keys: the code below assumes `tokens.account_id` and `tokens.access_token` exist for a ChatGPT login and that an API-key login has `OPENAI_API_KEY` set and no `tokens`. If the file says otherwise, change `read_account` to match it and say so in the commit.

- [ ] **Step 2: Write the failing tests**

`tests/test_quota_codex.py`:

```python
import json
from pathlib import Path

from aegis.quota.codex import PROVIDER, parse_limits, read_account

FIX = Path(__file__).parent / "fixtures" / "codex"


def test_a_chatgpt_login_has_an_account_and_an_api_key_has_none(tmp_path):
    assert read_account(FIX / "auth-chatgpt.json")
    key = tmp_path / "auth.json"
    key.write_text(json.dumps({"OPENAI_API_KEY": "sk-x", "tokens": None}))
    assert read_account(key) is None
    assert read_account(tmp_path / "missing.json") is None


def test_the_windows_are_named_by_their_length():
    snap = parse_limits(json.loads((FIX / "rate_limits.json").read_text()), now=5.0)
    assert [w.kind for w in snap.windows] == ["5h", "week"]
    assert all(0 <= w.percent <= 100 and w.resets_at is not None for w in snap.windows)
    assert snap.fetched_at == 5.0


def test_a_window_of_another_length_keeps_its_minutes_as_its_kind():
    r = {"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 60, "resetsAt": 1791600000},
                        "secondary": None}}  # fmt: skip
    (w,) = parse_limits(r, now=0.0).windows
    assert w.kind == "60m" and w.percent == 10.0


def test_the_provider_shows_two_windows_with_spans():
    assert PROVIDER.bar_windows == (("5h", "5 hours"), ("week", "week"))
    assert PROVIDER.window_spans == {"5h": 5 * 3600, "week": 7 * 86400}
    assert PROVIDER.harness == "codex"
```

Add to the existing quota tests (`grep -ln "turn_ended" tests/test_quota*.py`) one test that a `Quota` built with a Claude stub and a Codex stub nudges only the Codex service on `turn_ended("codex")`, modelled on the test there that checks `turn_ended()` nudges Claude.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_quota_codex.py -q`
Expected: FAIL: `No module named 'aegis.quota.codex'`.

- [ ] **Step 4: Write the provider**

`src/aegis/quota/codex.py`:

```python
"""Live ChatGPT-plan quota for Codex: the 5-hour and weekly windows.

Codex reports the plan's windows through its own app-server
(``account/rateLimits/read``), so this module asks a short-lived ``codex
app-server`` instead of an undocumented endpoint: about half a second and no
tokens. Only a ChatGPT login has windows; an API key or no login reads as no
credentials, and the gauge stays hidden.

Every failure path degrades to a note on the gauges; nothing raises into the
caller except ``QuotaError``.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .core import QuotaError, QuotaProvider, QuotaSnapshot, QuotaWindow, _severity, boot_clock

TIMEOUT_S = 15.0
KINDS = {300: "5h", 10080: "week"}


def auth_path() -> Path:
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(home) / "auth.json"


def read_account(path: Path | None = None) -> str | None:
    """The ChatGPT account id, or None when the login is not ChatGPT."""
    try:
        data = json.loads((path or auth_path()).read_text())
    except (OSError, ValueError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None
    return str(tokens.get("account_id") or "chatgpt")


def parse_limits(result: dict, *, now: float) -> QuotaSnapshot:
    rl = result.get("rateLimits") if isinstance(result.get("rateLimits"), dict) else {}
    windows: list[QuotaWindow] = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("usedPercent"), (int, float)):
            continue
        mins = w.get("windowDurationMins")
        resets = w.get("resetsAt")
        percent = float(w["usedPercent"])
        windows.append(
            QuotaWindow(
                kind=KINDS.get(mins, f"{mins}m"),
                percent=percent,
                severity=_severity(percent, None),
                resets_at=datetime.fromtimestamp(resets, timezone.utc)
                if isinstance(resets, int)
                else None,
                is_active=True,
            )
        )
    return QuotaSnapshot(windows=tuple(windows), fetched_at=now)


def fetch_limits(token: str, *, bin: str = "codex") -> QuotaSnapshot:
    """Ask a short-lived app-server. Blocking: callers run it off the loop."""
    try:
        p = subprocess.Popen(
            [bin, "app-server", "--disable", "plugins", "--disable", "remote_plugin"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True,
        )  # fmt: skip
    except OSError as e:
        raise QuotaError("unreachable") from e
    assert p.stdin is not None and p.stdout is not None
    try:
        for msg in (
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "aegis", "version": "quota"}}},
            {"method": "initialized"},
            {"id": 2, "method": "account/rateLimits/read", "params": {}},
        ):  # fmt: skip
            p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()
        for line in p.stdout:
            m = json.loads(line)
            if m.get("id") != 2:
                continue
            if "error" in m:
                raise QuotaError("unauthorized")
            return parse_limits(m.get("result") or {}, now=boot_clock())
        raise QuotaError("unreachable")
    except (OSError, ValueError) as e:
        raise QuotaError("unreachable") from e
    finally:
        p.stdin.close()
        try:
            p.wait(TIMEOUT_S)
        except subprocess.TimeoutExpired:
            p.kill()
        try:
            os.killpg(p.pid, 9)
        except ProcessLookupError:
            pass


PROVIDER = QuotaProvider(
    name="codex",
    label="ChatGPT",
    harness="codex",
    bar_windows=(("5h", "5 hours"), ("week", "week")),
    fetch=fetch_limits,
    read_token=read_account,
    account=read_account,
    window_spans={"5h": 5 * 3600, "week": 7 * 86400},
)
```

`src/aegis/quota/__init__.py`: import `from .codex import PROVIDER as CODEX`, `PROVIDERS = (CLAUDE, OPENCODE_GO, CODEX)`, the module docstring names the Codex cadence ("Codex every 60 s"), and `turn_ended` matches on harness:

```python
    def turn_ended(self, harness: str = "claude-code") -> None:
        """A turn just spent quota: refresh the provider that harness spends
        before its cadence would, but no sooner than its turn floor."""
        if self._task is None:
            return
        for p in self._providers:
            if p.harness == harness and p.name != OPENCODE_GO.name:
                t = asyncio.create_task(self._nudge(p))
                self._nudges.add(t)
                t.add_done_callback(self._nudges.discard)
```

(OpenCode Go was never nudged; keep it that way.)

`src/aegis/registry.py:93`: `self.quota.turn_ended(session.harness.name)`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_quota_codex.py $(ls tests/test_quota*.py) -q`
Expected: PASS.

- [ ] **Step 6: See it in a browser**

Start a dev server on a scratch root (never the Workspace's live state):

```bash
mkdir -p /tmp/aegis-codex-quota && cd /tmp/aegis-codex-quota && printf 'agents:\n  cx: {harness: codex, model: openai/gpt-5.5, effort: medium, permission: read}\n' > .aegis.yaml && AEGIS_QUOTA_CACHE=/tmp/aegis-codex-quota/cache uv run --project /home/apiad/Workspace/repos/aegis/.claude/worktrees/codex-harness-design aegis serve --port 8931
```

Open it, open a session panel's Usage row and check a "ChatGPT" gauge with two bars appears. Stop the server with Ctrl-C.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(quota): ChatGPT plan windows for Codex through account/rateLimits/read" -- src/aegis/quota/codex.py src/aegis/quota/__init__.py src/aegis/registry.py tests/test_quota_codex.py tests/fixtures/codex/rate_limits.json tests/fixtures/codex/auth-chatgpt.json $(git diff --name-only -- tests/test_quota*.py)
```

---

### Task 8: The live test and the bench

**Files:**
- Modify: `tests/test_live.py` (append after `test_a_real_opencode_session`)
- Modify: `scripts/bench.py:51-110,445` (a Codex scenario)

**Interfaces:**
- Consumes: everything above; `App(..., codex_bin=...)`; `tests/test_agents._free_port`.

- [ ] **Step 1: Write the live test**

Append to `tests/test_live.py`:

```python
CODEX_FREE = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


async def test_a_real_codex_session(tmp_path: Path, monkeypatch):
    """A prompt, an aegis tool call, a steer, an interrupt, a resume that keeps
    the context, and a read session whose write fails. A free model: costs
    nothing, and a 429 from its shared pool skips the test."""
    import asyncio
    import os

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    codex = shutil.which("codex")
    if not codex or not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("needs codex on PATH and OPENROUTER_API_KEY")
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        "[model_providers.openrouter]\n"
        'name = "OpenRouter"\n'
        'base_url = "https://openrouter.ai/api/v1"\n'
        'env_key = "OPENROUTER_API_KEY"\n'
        'wire_api = "responses"\n'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        f"  cx: {{harness: codex, model: {CODEX_FREE}, effort: high, permission: full}}\n"
        f"  reader: {{harness: codex, model: {CODEX_FREE}, effort: high, permission: read}}\n"
    )
    port = _free_port()
    app = App(make_roots(tmp_path, None), codex_bin=codex, base_url=f"http://127.0.0.1:{port}")
    server = uvicorn.Server(
        uvicorn.Config(build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")

    def tools(s) -> list[dict]:
        return [e for e in s.entries() if e["kind"] == "tool"]

    def prose(s) -> list[str]:
        return [e["md"] for e in s.entries() if e["kind"] == "prose"]

    async def turn(s, text: str) -> None:
        await s.send(text)
        await until(lambda: s.status == "working", timeout=30, what="the turn to start")
        await until(lambda: s.status == "idle", timeout=180, what=f"the turn {text!r}")
        if any("429" in e["summary"] for e in s.entries() if e["kind"] == "error"):
            pytest.skip("the free model is rate-limited upstream")

    try:
        r = await app.registry.call("session.spawn", {"agent": "cx"})
        s = app.sessions.sessions[r["log_id"]]
        await turn(s, "Remember the word PELICAN. Reply with the single word OK.")
        assert s.context_window and s.resume_id and s.cost_usd == 0

        await turn(s, "Call the aegis meta tool, then tell me in one line what it returned.")
        assert any(t["title"] == "meta" and t["status"] == "ok" for t in tools(s))

        await s.send("Run exactly this shell command in the foreground: sleep 40")
        await until(lambda: any(t["status"] == "running" for t in tools(s)), timeout=120, what="the call")
        await s.interrupt()
        await until(lambda: s.status == "idle", timeout=20, what="idle after the interrupt")
        assert tools(s)[-1]["status"] == "err"

        sid = s.resume_id
        await s.stop()
        await turn(s, "What word did I ask you to remember? Reply with that one word.")
        assert "PELICAN" in prose(s)[-1].upper() and s.resume_id == sid

        r = await app.registry.call("session.spawn", {"agent": "reader"})
        reader = app.sessions.sessions[r["log_id"]]
        await turn(reader, "Run this shell command: echo hi > x.txt")
        assert not (tmp_path / "x.txt").exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
        await app.shutdown()
```

- [ ] **Step 2: Run it**

Run: `OPENROUTER_API_KEY=$(cat /home/apiad/Workspace/.claude/openrouter.token) uv run pytest tests/test_live.py -m live -k codex -q`
Expected: PASS (or SKIP with "rate-limited upstream"; re-run once later before reporting a skip). Check the meta tool title: the OpenCode test asserts `t["title"] == "meta"` for `aegis_meta`; Codex names it `mcp__aegis__meta`, which `describe.py` titles the same way. If the title differs, assert on what `describe.py` gives `mcp__aegis__meta` for Claude.

- [ ] **Step 3: Add the bench scenario**

In `scripts/bench.py`, next to `OPENCODE_FIXTURE` and `opencode_lines()`:

```python
CODEX_FIXTURE = ROOT / "tests" / "fixtures" / "codex" / "tool.jsonl"


def codex_lines() -> list[str]:
    return [ln for ln in CODEX_FIXTURE.read_text().splitlines() if ln]
```

`server_cost(harness=...)` picks lines and ids by harness (lines 70-72); extend both choices:

```python
    lines = {"opencode": opencode_lines, "codex": codex_lines}.get(harness, claude_lines)()
    ids = {"opencode": ('"call_', '"prt_', '"msg_'), "codex": ('"call-', '"msg_', '"rs_')}.get(
        harness, ('"toolu_',)
    )
```

and give its fake binary and metric keys a Codex branch where the OpenCode ones are chosen (`opencode_bin="true"` becomes `codex_bin="true"` for `harness == "codex"`; the metric keys `server_line_codex` and `server_codex_lines`). Add `metrics.update(asyncio.run(server_cost(harness="codex")))` after the OpenCode line (445). Run `uv run python scripts/bench.py` and check the new keys print.

- [ ] **Step 4: Commit**

```bash
git commit -m "test(codex): a live session on a free model and a bench scenario" -- tests/test_live.py scripts/bench.py
```

---

### Task 9: Docs, the full gate, and the PR

**Files:**
- Modify: `DESIGN.md` (the harness paragraph, lines 143-153, and "A session outlives its process", lines 32-38)
- Modify: `docs/superpowers/specs/2026-10-09-aegis-2-codex-harness-design.md` (status)
- Create: `changelog.d/105-codex-harness.added.md`
- Modify: `README.md` if it lists the harnesses (`grep -n -i opencode README.md`)

- [ ] **Step 1: DESIGN.md**

In "A session outlives its process", the harness list reads "(a harness child, `claude`, `opencode serve` or `codex app-server`, is running)" and the resume sentence adds "or `thread/resume` on the same Codex thread". In "A harness is a module behind one interface", add after the OpenCode sentence:

```
Codex (`codex/process.py`) is one `codex app-server` per session over JSON-RPC
on stdio, for the same reason, and the only harness whose model, effort and
sandbox travel with every turn, so a change never restarts it. Its process
writes `aegis/*` lines of its own for what only a response carries (the
version, the thread, each turn's model), and kills its process group after
any exit, because a grandchild of a dead child held the thread's writer lease.
```

and "Both parsers emit the same events" becomes "The parsers emit the same events".

- [ ] **Step 2: Changelog fragment**

`changelog.d/105-codex-harness.added.md`:

```markdown
- **Codex sessions.** An agent whose harness is `codex` runs `codex app-server`
  and behaves like a Claude or OpenCode session: streamed text, mid-turn prompts
  through `turn/steer`, interrupts, resume, aegis tools over MCP, subagents as
  Task rows, and model, effort and permission changes that need no restart. The
  card prices OpenAI models and free OpenRouter models, and a ChatGPT login shows
  its 5-hour and weekly windows in Usage. A model is `provider/model`, such as
  `openai/gpt-5.5` or `openrouter/nvidia/nemotron-3-super-120b-a12b:free`.
```

(If Task 7 is still blocked, drop the ChatGPT sentence and say in the PR body that the gauge follows.)

- [ ] **Step 3: Spec status**

Change the spec's status line to `> **Status:** implemented, <date>.` and correct anything the build changed (the spec already records the simplifications made while planning).

- [ ] **Step 4: The full gate**

Run, and read the exit code directly:

```bash
make check; echo "make check exit: $?"
make lint-docs; echo "lint-docs exit: $?"
make format-check; echo "format-check exit: $?"
make bench
```

Expected: exit 0 for the first three. Fix what fails, then re-run. Keep the bench table for the PR body.

- [ ] **Step 5: Exercise it the way a person reaches it**

Start a server after the last change, in a scratch root with its own Codex config (as in the live test), spawn the `cx` agent from the new tab in a browser, send a prompt, watch text stream, send a second prompt while a `sleep 10` runs, and interrupt one. Write what you saw in the PR body.

- [ ] **Step 6: Commit, push, open the PR**

```bash
git commit -m "docs: Codex in DESIGN.md, the spec's status, and a changelog fragment" -- DESIGN.md docs/superpowers/specs/2026-10-09-aegis-2-codex-harness-design.md changelog.d/105-codex-harness.added.md
git push
gh pr create --title "feat: Codex sessions through codex app-server (#105)" --body-file - <<'EOF'
Closes #105. Spec: docs/superpowers/specs/2026-10-09-aegis-2-codex-harness-design.md; plan: docs/superpowers/plans/2026-10-09-aegis-2-codex-harness.md.

<what was measured, what the browser showed, the bench table, what was left out>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

Then stop: Alex smoke-tests the branch with `AEGIS_REF=codex-harness-design aegis-dev` and merges.
