"""A stand-in for ``opencode serve``, for tests and the bench.

It serves the v1 routes aegis uses on the port it prints, checks the basic
auth OpenCode checks, and emits events in the shapes OpenCode 1.18.31 emits
(``tests/fixtures/opencode/``). The text of a prompt picks a script:

    /sleep N        a bash call that takes N seconds, then text. Prompts sent
                    meanwhile are echoed at once and answered after the call,
                    in the same turn, as OpenCode reads them at the next step.
    /stall N        N seconds busy with no step, then text.
    /stream N       N words of text, one delta every 0.25 s.
    /fail           a bash call that fails.
    /bash D => OUT  a bash call described D whose output is OUT.
    /edit P         an edit of file P from "a" to "b" (no file is touched).
    /mcp T JSON     call tool T of the aegis MCP server in OPENCODE_CONFIG_CONTENT
                    with arguments JSON, as an aegis_T tool part.
    /task           a task call whose child session says one thing.
    /body           text "body: <JSON of the prompt body received>".
    /config         text "config: <OPENCODE_CONFIG_CONTENT>".
    /recall         text listing the prompts this session id received before.
    /exit N         a stderr line, then exit with code N.
    anything else   text "you said: <prompt>".

Text streams as an empty part, three deltas, then the full text. An assistant
message costs $0.002 and reports 1,030 tokens. After its first turn a session
is titled "Fake title: <first prompt>". ``/session/{id}/abort`` ends a running
script as OpenCode does: ``session.error`` (MessageAbortedError), idle, the
aborted tool part completed, idle again. ``/session/{id}/command`` echoes the
expanded template (``hello``: "Say hello to <args>."), runs it, and answers
when the turn ends; an unknown command is a 400.

Each session id's prompts are appended to ``$FAKE_OPENCODE_HOME/<id>.prompts``,
which is also how ``GET /session/{id}`` knows a session an earlier process
made. ``FAKE_OPENCODE_LOG=<file>`` gets one ``METHOD path`` line per request.
``FAKE_OPENCODE_DIE=1`` exits with code 1 before listening.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "1.18.31"
CONFIG = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD")
HOME = os.environ.get("FAKE_OPENCODE_HOME") or tempfile.gettempdir()
LOG = os.environ.get("FAKE_OPENCODE_LOG")
COST = 0.002
TOKENS = {
    "total": 1030,
    "input": 1000,
    "output": 20,
    "reasoning": 10,
    "cache": {"read": 0, "write": 0},
}
ZERO = {
    "total": 0,
    "input": 0,
    "output": 0,
    "reasoning": 0,
    "cache": {"read": 0, "write": 0},
}
TEMPLATES = {"hello": "Say hello to $ARGUMENTS."}
COMMANDS = [
    {"name": "hello", "description": "Greet someone.", "source": "command"},
    {"name": "review", "description": "Review the changes.", "source": "command"},
    {
        "name": "unslop",
        "description": "Cut AI tells from any writing.",
        "source": "skill",
    },
]
PROVIDERS = {
    "providers": [
        {
            "id": "opencode-go",
            "name": "OpenCode Go",
            "models": {
                "fake-pro": {"id": "fake-pro", "name": "Fake Pro", "limit": {"context": 1000000, "output": 8192},
                             "variants": {"high": {}, "max": {}}},
                "fake-flash": {"id": "fake-flash", "name": "Fake Flash", "limit": {"context": 500000, "output": 8192},
                               "variants": {"low": {}, "high": {}, "max": {}}},
                "fake-plain": {"id": "fake-plain", "name": "Fake Plain", "limit": {"context": 200000, "output": 8192}},
            },
        }
    ],
    "default": {"opencode-go": "fake-flash"},
}  # fmt: skip

_lock = threading.Lock()
_n = [0]
subscribers: list[queue.Queue] = []
sessions: dict[str, "Sess"] = {}


def nid(prefix: str) -> str:
    with _lock:
        _n[0] += 1
        return f"{prefix}_{_n[0]:06d}{uuid.uuid4().hex[:8]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def emit(type_: str, **props) -> None:
    line = json.dumps({"id": nid("evt"), "type": type_, "properties": props})
    with _lock:
        subs = list(subscribers)
    for q in subs:
        q.put(line)


class Aborted(Exception):
    pass


class Sess:
    def __init__(self, sid: str, parent: str | None = None) -> None:
        self.id, self.parent = sid, parent
        self.title = f"New session - {time.strftime('%Y-%m-%dT%H:%M:%S')}"
        self.model: dict | None = None
        self.inbox: queue.Queue = queue.Queue()
        self.abort = threading.Event()
        self.cond = threading.Condition()
        self.turns = 0
        self.first: str | None = None
        self.titled = False
        if parent is None:
            threading.Thread(target=worker, args=(self,), daemon=True).start()

    def info(self) -> dict:
        d = {"id": self.id, "slug": "fake-slug", "version": VERSION, "directory": os.getcwd(),
             "title": self.title, "time": {"created": now_ms(), "updated": now_ms()}}  # fmt: skip
        if self.parent:
            d["parentID"] = self.parent
        if self.model:
            d["model"] = self.model
        return d

    def end_turn(self) -> None:
        with self.cond:
            self.turns += 1
            self.cond.notify_all()


def prompts_file(sid: str) -> str:
    return os.path.join(HOME, f"{sid}.prompts")


def take_prompt(s: Sess, text: str, body: dict) -> None:
    with open(prompts_file(s.id), "a") as f:
        f.write(json.dumps(text) + "\n")
    m = body.get("model")
    if isinstance(m, str):
        p, _, i = m.partition("/")
        m = {"providerID": p, "modelID": i}
    if isinstance(m, dict) and m:
        s.model = {"id": m.get("modelID"), "providerID": m.get("providerID"),
                   "variant": body.get("variant") or "default"}  # fmt: skip
    mid = nid("msg")
    emit("message.updated", sessionID=s.id,
         info={"id": mid, "role": "user", "sessionID": s.id, "time": {"created": now_ms()}})  # fmt: skip
    emit("message.part.updated", sessionID=s.id, time=now_ms(),
         part={"id": nid("prt"), "messageID": mid, "sessionID": s.id, "type": "text", "text": text})  # fmt: skip
    emit("session.updated", sessionID=s.id, info=s.info())
    emit("session.diff", sessionID=s.id, diff=[])  # noise aegis drops
    s.inbox.put((text, body))


class Msg:
    def __init__(self, s: Sess) -> None:
        self.s, self.id, self.created = s, nid("msg"), now_ms()
        self.update()

    def update(
        self, finish: str | None = None, error: str | None = None, done: bool = False
    ) -> None:
        m = self.s.model or {"id": "fake-flash", "providerID": "opencode-go"}
        info = {"id": self.id, "role": "assistant", "sessionID": self.s.id, "mode": "build",
                "modelID": m["id"], "providerID": m["providerID"],
                "cost": COST if done else 0, "tokens": TOKENS if done else ZERO,
                "time": {"created": self.created, **({"completed": now_ms()} if done else {})}}  # fmt: skip
        if finish:
            info["finish"] = finish
        if error:
            info["error"] = {"name": error, "data": {"message": "Aborted"}}
        emit("message.updated", sessionID=self.s.id, info=info)

    def part(self, pid: str | None = None, **p) -> str:
        pid = pid or nid("prt")
        emit("message.part.updated", sessionID=self.s.id, time=now_ms(),
             part={"id": pid, "messageID": self.id, "sessionID": self.s.id, **p})  # fmt: skip
        return pid

    def text(
        self, text: str, pace: float = 0.0, chunks: list[str] | None = None
    ) -> None:
        pid = self.part(type="text", text="")
        if chunks is None:
            k = max(1, len(text) // 3)
            chunks = [text[:k], text[k : 2 * k], text[2 * k :]]
        for c in chunks:
            emit(
                "message.part.delta",
                sessionID=self.s.id,
                messageID=self.id,
                partID=pid,
                field="text",
                delta=c,
            )
            if pace and self.s.abort.wait(pace):
                raise Aborted
        self.part(pid, type="text", text=text)

    def step(self, reason: str) -> None:
        self.part(type="step-finish", reason=reason, tokens=TOKENS, cost=COST)
        self.update(reason, done=True)

    def tool_start(self, name: str, inp: dict) -> tuple[str, str]:
        call = nid("call")
        pid = self.part(
            type="tool",
            tool=name,
            callID=call,
            state={"status": "pending", "input": {}, "raw": ""},
        )
        self.part(pid, type="tool", tool=name, callID=call,
                  state={"status": "running", "input": inp, "time": {"start": now_ms()}})  # fmt: skip
        return pid, call

    def tool_end(self, pid: str, call: str, name: str, inp: dict, output: str | None = None,
                 error: str | None = None) -> None:  # fmt: skip
        state = ({"status": "error", "input": inp, "error": error} if error is not None
                 else {"status": "completed", "input": inp, "output": output or "", "metadata": {}})  # fmt: skip
        self.part(pid, type="tool", tool=name, callID=call, state=state)

    def tool(self, name: str, inp: dict, output: str | None = None, error: str | None = None,
             wait: float = 0.0) -> None:  # fmt: skip
        pid, call = self.tool_start(name, inp)
        if wait and self.s.abort.wait(wait):
            abort_turn(self.s)
            self.tool_end(pid, call, name, inp,
                          output="(no output)\n\n<shell_metadata>\nUser aborted the command\n</shell_metadata>")  # fmt: skip
            self.update(error="MessageAbortedError", done=True)
            idle_events(self.s)
            raise Aborted
        self.tool_end(pid, call, name, inp, output=output, error=error)


def idle_events(s: Sess) -> None:
    emit("session.status", sessionID=s.id, status={"type": "idle"})
    emit("session.idle", sessionID=s.id)


def abort_turn(s: Sess) -> None:
    emit(
        "session.error",
        sessionID=s.id,
        error={"name": "MessageAbortedError", "data": {"message": "Aborted"}},
    )
    idle_events(s)


def drain(s: Sess) -> list[str]:
    out = []
    while True:
        try:
            out.append(s.inbox.get_nowait()[0])
        except queue.Empty:
            return out


def mcp_call(tool: str, arguments: dict) -> tuple[bool, str]:
    cfg = (CONFIG.get("mcp") or {}).get("aegis")
    if not cfg:
        return False, "no aegis MCP server configured"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}})  # fmt: skip
    req = urllib.request.Request(cfg["url"], data=body.encode(), headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        **cfg.get("headers", {})})  # fmt: skip
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


def after_tool(s: Sess, m: Msg, text: str) -> None:
    m.step("tool-calls")
    extra = drain(s)
    m2 = Msg(s)
    m2.part(type="step-start")
    m2.text(text + "".join(f"\nalso: {e}" for e in extra))
    m2.step("stop")


def run(s: Sess, text: str, body: dict) -> None:
    if s.first is None:
        s.first = text
    word, _, rest = text.partition(" ")
    if word == "/stall":
        if s.abort.wait(float(rest or 1)):
            abort_turn(s)
            raise Aborted
    if word == "/exit":
        print("fake opencode: exiting on request", file=sys.stderr, flush=True)
        os._exit(int(rest or 1))
    m = Msg(s)
    m.part(type="step-start")
    if word == "/sleep":
        m.tool("bash", {"command": f"sleep {rest}", "description": f"sleep {rest}"},
               output=f"slept {rest}\n", wait=float(rest or 1))  # fmt: skip
        return after_tool(s, m, f"done after {rest}")
    if word == "/fail":
        m.tool("bash", {"command": "false"}, error="exit code 1")
        return after_tool(s, m, "it failed")
    if word == "/bash":
        d, _, out = rest.partition(" => ")
        m.tool("bash", {"command": d, "description": d}, output=out)
        return after_tool(s, m, "ran it")
    if word == "/edit":
        m.tool(
            "edit",
            {"filePath": rest, "oldString": "a", "newString": "b"},
            output="Edit applied successfully.",
        )
        return after_tool(s, m, "edited")
    if word == "/mcp":
        tool, _, args = rest.partition(" ")
        arguments = json.loads(args or "{}")
        ok, out = mcp_call(tool, arguments)
        m.tool(
            f"aegis_{tool}",
            arguments,
            output=out if ok else None,
            error=None if ok else out,
        )
        return after_tool(s, m, "called it")
    if word == "/task":
        inp = {
            "description": "look around",
            "prompt": "List the files.",
            "subagentType": "general",
        }
        pid, call = m.tool_start("task", inp)
        child = Sess(nid("ses"), parent=s.id)
        sessions[child.id] = child
        emit("session.created", sessionID=child.id, info=child.info())
        cu = nid("msg")
        emit("message.updated", sessionID=child.id,
             info={"id": cu, "role": "user", "sessionID": child.id, "time": {"created": now_ms()}})  # fmt: skip
        emit("message.part.updated", sessionID=child.id, time=now_ms(),
             part={"id": nid("prt"), "messageID": cu, "sessionID": child.id, "type": "text", "text": "List the files."})  # fmt: skip
        cm = Msg(child)
        cm.part(type="step-start")
        cm.text("child looked around")
        cm.step("stop")
        idle_events(child)
        m.tool_end(pid, call, "task", inp, output="child done")
        return after_tool(s, m, "the child is done")
    if word == "/stream":
        words = [f"chunk{i} " for i in range(1, int(rest or 4) + 1)]
        m.text("".join(words), pace=0.25, chunks=words)
    elif word == "/body":
        m.text("body: " + json.dumps(body, sort_keys=True))
    elif word == "/config":
        m.text("config: " + json.dumps(CONFIG, sort_keys=True))
    elif word == "/recall":
        with open(prompts_file(s.id)) as f:
            earlier = [json.loads(x) for x in f.read().splitlines()][:-1]
        m.text("earlier: " + json.dumps(earlier))
    else:
        m.text(f"you said: {text}")
    m.step("stop")


def worker(s: Sess) -> None:
    while True:
        item = s.inbox.get()
        s.abort.clear()
        emit("session.status", sessionID=s.id, status={"type": "busy"})
        try:
            while True:
                run(s, *item)
                try:
                    item = s.inbox.get_nowait()
                except queue.Empty:
                    break
        except Aborted:
            drain(s)
            s.end_turn()
            continue
        if not s.titled and s.first:
            s.titled, s.title = True, f"Fake title: {s.first[:40]}"
            emit("session.updated", sessionID=s.id, info=s.info())
        idle_events(s)
        s.end_turn()


def new_session() -> Sess:
    s = Sess(nid("ses"))
    sessions[s.id] = s
    emit("session.created", sessionID=s.id, info=s.info())
    emit("session.updated", sessionID=s.id, info=s.info())
    emit("plugin.added", id="core/fake")  # noise aegis drops
    return s


def find(sid: str) -> Sess | None:
    s = sessions.get(sid)
    if s is None and os.path.exists(prompts_file(sid)):
        s = sessions[sid] = Sess(sid)
    return s


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _json(self, code: int, obj) -> None:
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        if data:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        if not PASSWORD:
            return True
        want = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        if self.headers.get("authorization") == want:
            return True
        self._json(401, {"name": "Unauthorized"})
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        if LOG:
            with open(LOG, "a") as f:
                f.write(f"{method} {path}\n")
        if not self._authed():
            return
        if method == "GET" and path == "/global/health":
            return self._json(200, {"healthy": True, "version": VERSION})
        if method == "GET" and path == "/event":
            return self._events()
        if method == "GET" and path == "/command":
            return self._json(200, COMMANDS)
        if method == "GET" and path == "/config/providers":
            return self._json(200, PROVIDERS)
        if method == "POST" and path == "/session":
            self._body()
            return self._json(200, new_session().info())
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "session":
            s = find(parts[1])
            if s is None:
                return self._json(404, {"name": "NotFoundError"})
            if method == "GET" and len(parts) == 2:
                return self._json(200, s.info())
            body = self._body()
            if parts[2:] == ["prompt_async"]:
                text = "".join(p.get("text", "") for p in body.get("parts") or [])
                take_prompt(s, text, body)
                return self._json(204, None)
            if parts[2:] == ["abort"]:
                s.abort.set()
                return self._json(200, True)
            if parts[2:] == ["command"]:
                name = body.get("command")
                if name not in {c["name"] for c in COMMANDS}:
                    return self._json(
                        400, {"name": "CommandNotFound", "data": {"command": name}}
                    )
                text = TEMPLATES.get(name, f"Run {name}.").replace(
                    "$ARGUMENTS", body.get("arguments", "")
                )
                with s.cond:
                    before = s.turns
                take_prompt(s, text, body)
                with s.cond:
                    s.cond.wait_for(lambda: s.turns > before, timeout=60)
                return self._json(200, {"info": {"role": "assistant"}, "parts": []})
        return self._json(404, {"name": "NotFound"})

    def _events(self) -> None:
        q: queue.Queue = queue.Queue()
        with _lock:
            subscribers.append(q)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(
                b'data: {"id":"evt_0","type":"server.connected","properties":{}}\n\n'
            )
            self.wfile.flush()
            while True:
                try:
                    line = q.get(timeout=10)
                except queue.Empty:
                    line = json.dumps(
                        {"id": nid("evt"), "type": "server.heartbeat", "properties": {}}
                    )
                self.wfile.write(f"data: {line}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _lock:
                subscribers.remove(q)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] != "serve":
        print("fake opencode: only `serve` is faked", file=sys.stderr)
        sys.exit(2)
    if os.environ.get("FAKE_OPENCODE_DIE"):
        print("fake opencode: dying before listening", file=sys.stderr, flush=True)
        sys.exit(1)
    port = int(args[args.index("--port") + 1]) if "--port" in args else 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(
        f"opencode server listening on http://127.0.0.1:{srv.server_address[1]}",
        flush=True,
    )
    srv.serve_forever()


if __name__ == "__main__":
    main()
