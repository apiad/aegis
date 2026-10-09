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
signal ends it. ``FAKE_CODEX_FAIL_RESUME=1`` refuses every ``thread/resume``.
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
        if a.startswith("mcp_servers.") and ".url=" in a:
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
    text = "\n".join(
        c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text"
    )
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
            note(
                "item/started",
                threadId=self.tid,
                turnId=self.id,
                item={**item, "status": "inProgress"},
            )
        note("item/completed", threadId=self.tid, turnId=self.id, item=item)

    def user(self, text: str) -> None:
        with open(prompts_file(self.tid), "a") as f:
            f.write(text.replace("\n", " ") + "\n")
        self.item({"type": "userMessage", "id": f"um-{uuid.uuid4().hex[:8]}",
                   "content": [{"type": "text", "text": text, "text_elements": []}]})  # fmt: skip

    def say(self, text: str, delay: float = 0.0) -> None:
        iid = f"msg-{uuid.uuid4().hex[:8]}"
        note(
            "item/started",
            threadId=self.tid,
            turnId=self.id,
            item={"type": "agentMessage", "id": iid, "text": ""},
        )
        words = text.split(" ")
        chunks = (
            [" ".join(words[i::3]) for i in range(3)]
            if delay == 0
            else [w + " " for w in words]
        )
        for c in chunks:
            if delay:
                time.sleep(delay)
            note(
                "item/agentMessage/delta",
                threadId=self.tid,
                turnId=self.id,
                itemId=iid,
                delta=c,
            )
        full = text if delay == 0 else "".join(chunks)
        note(
            "item/completed",
            threadId=self.tid,
            turnId=self.id,
            item={"type": "agentMessage", "id": iid, "text": full},
        )

    def shell(
        self, command: str, output: str, code: int = 0, wait: float = 0.0
    ) -> bool:
        """A shell call; False when an interrupt ended it."""
        iid = f"call-{uuid.uuid4().hex[:8]}"
        item = {"type": "commandExecution", "id": iid, "command": f"/bin/bash -lc '{command}'",
                "commandActions": [{"type": "unknown", "command": command}], "cwd": os.getcwd()}  # fmt: skip
        note(
            "item/started",
            threadId=self.tid,
            turnId=self.id,
            item={**item, "status": "inProgress"},
        )
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
    note(
        "turn/started",
        threadId=t.tid,
        turn={"id": t.id, "status": "inProgress", "items": []},
    )
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
        change = {
            "path": args,
            "kind": {"type": "update"},
            "diff": "@@ -1 +1 @@\n-a\n+b\n",
        }
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
        note(
            "turn/started",
            threadId=child,
            turn={"id": sub.id, "status": "inProgress", "items": []},
        )
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
    if method == "thread/resume" and os.environ.get("FAKE_CODEX_FAIL_RESUME"):
        raise ValueError("the fake refuses to resume")
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
        STATE["steer"] += [
            i.get("text", "") for i in p.get("input") or [] if i.get("type") == "text"
        ]
        return {"turnId": STATE["turn"]}
    if method == "turn/interrupt":
        STATE["stop"].set()
        return {}
    if method == "thread/compact/start":
        t = Turn(STATE["thread"])
        STATE["turn"] = t.id
        note(
            "turn/started",
            threadId=t.tid,
            turn={"id": t.id, "status": "inProgress", "items": []},
        )
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
