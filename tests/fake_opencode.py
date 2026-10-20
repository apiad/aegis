"""A stand-in for ``opencode serve``, for tests and the bench.

It serves the ``/api`` routes aegis uses on the port it prints, checks the
basic auth OpenCode checks, and emits events in the shapes OpenCode 2.0.28
emits (``tests/fixtures/opencode/``). Any other path answers with an HTML page,
as the real server's web app does. aegis sends every ``/`` line to
``/api/session/{id}/command``, so the scripts are commands, listed by
``/api/command`` and echoed as typed:

    /sleep N        a shell call that takes N seconds, then text. Prompts sent
                    meanwhile are queued at once and answered after the call,
                    in the same turn, as OpenCode reads them at the next step.
    /stall N        N seconds busy with no step, then text.
    /stream N       N words of text, one delta every 0.25 s.
    /fail           a shell call that fails.
    /bash D => OUT  a shell call described D whose output is OUT.
    /edit P         an edit of file P from "a" to "b" (no file is touched).
    /mcp T JSON     call tool T of the aegis MCP server in OPENCODE_CONFIG_CONTENT
                    with arguments JSON, as an aegis_T tool call.
    /task           a subagent call whose child session says one thing.
    /state          text "state: <JSON of the session's model, variant,
                    instructions and the last prompt's skills>".
    /config         text "config: <OPENCODE_CONFIG_CONTENT>".
    /recall         text listing the prompts this session id received before.
    /exit N         a stderr line, then exit with code N.

A plain prompt is answered with text "you said: <prompt>".

Text streams as an open, three deltas, then the full text. A step costs $0.002
and reports 1,030 tokens. After its first turn a session is renamed "Fake title:
<first prompt>". ``/interrupt`` ends a running script as OpenCode does: the
open call fails as ``aborted``, then ``session.execution.interrupted``.
``/command`` queues the expanded template (``hello``: "Say hello to <args>.")
and returns at once; an unknown command is a 404. The command list is empty
until the first read of it, and fills a moment later with ``command.updated``.

Each session id's prompts are appended to ``$FAKE_OPENCODE_HOME/<id>.prompts``,
which is also how ``GET /api/session/{id}`` knows a session an earlier process
made. ``FAKE_OPENCODE_LOG=<file>`` gets one ``METHOD path`` line per request.
``/api/mcp`` says the configured servers are pending at its first read and
connected after. ``FAKE_OPENCODE_DIE=1`` exits with code 1 before listening;
``FAKE_OPENCODE_FAIL_SESSION=1`` answers ``POST /api/session`` with a 500. The
log also gets ``START <pid>`` when the server starts, so a test can check every
child it caused is gone. A prompt whose text is ``FAIL500`` is answered 500.
``FAKE_OPENCODE_EXHAUSTED=<id>,<id>`` names models (``provider/model``) whose
every turn fails with Zen's ``429 FreeUsageLimitError``, as an exhausted free
model's does. A prompt ``OVERFLOW`` fails its turn with a
``ContextOverflowError``.
"""

from __future__ import annotations

import sys

if sys.argv[1:2] == ["--version"]:
    # First, before the imports below: aegis reads this before every launch
    # on a new binary (harness_versions.py), and the whole module costs
    # 0.15 s to import. The tested max, or $AEGIS_FAKE_HARNESS_VERSION; empty
    # prints nothing and exits 2, as a harness with no --version does.
    import os
    import tomllib

    _v = os.environ.get("AEGIS_FAKE_HARNESS_VERSION")
    if _v is None:
        _toml = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "aegis",
            "data",
            "tested-harnesses.toml",
        )
        with open(_toml, "rb") as _f:
            _v = tomllib.load(_f)["opencode"]["max"]
    if not _v:
        sys.exit(2)
    print("{}".format(_v))
    sys.exit(0)

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

VERSION = "2.0.28"
CONFIG = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD")
HOME = os.environ.get("FAKE_OPENCODE_HOME") or tempfile.gettempdir()
LOG = os.environ.get("FAKE_OPENCODE_LOG")
EXHAUSTED = {m for m in os.environ.get("FAKE_OPENCODE_EXHAUSTED", "").split(",") if m}
LIMIT = {
    "type": "APIError",
    "message": "429 FreeUsageLimitError: free usage limit reached",
}
OVERFLOW = {"type": "ContextOverflowError", "message": "prompt is too long"}
COST = 0.002
TOKENS = {
    "input": 1000,
    "output": 20,
    "reasoning": 10,
    "cache": {"read": 0, "write": 0},
}
TEMPLATES = {"hello": "Say hello to $ARGUMENTS."}
SCRIPTS = (
    "sleep",
    "stall",
    "stream",
    "fail",
    "bash",
    "edit",
    "mcp",
    "task",
    "state",
    "config",
    "recall",
    "exit",
)
COMMANDS = [
    *({"name": w, "description": f"Fake script {w}."} for w in SCRIPTS),
    {"name": "hello", "description": "Greet someone."},
    {"name": "review", "description": "Review the changes."},
]
SKILLS = [
    {"id": "unslop", "name": "Unslop", "description": "Cut AI tells from any writing."}
]


def _model(mid: str, name: str, context: int, variants=(), cost=(1.0, 4.0),
           tools=True, output=("text",)) -> dict:  # fmt: skip
    return {"id": mid, "modelID": mid, "providerID": "opencode-go", "name": name,
            "capabilities": {"tools": tools, "input": ["text"], "output": list(output)},
            "variants": [{"id": v, "settings": {}} for v in variants],
            "cost": [{"input": cost[0], "output": cost[1], "cache": {"read": 0, "write": 0}}],
            "status": "active", "enabled": True, "limit": {"context": context, "output": 8192}}  # fmt: skip


MODELS = [
    _model("fake-pro", "Fake Pro", 1000000, ("high", "max")),
    # Free, but it makes video and calls no tools: no agent can run on it.
    _model(
        "fake-video", "Fake Video", 8000, cost=(0, 0), tools=False, output=("video",)
    ),
    _model("fake-flash", "Fake Flash", 500000, ("low", "high", "max"), cost=(0, 0)),
    _model("fake-plain", "Fake Plain", 200000),
    {**_model("fake-off", "Fake Off", 1000), "enabled": False},
]
HTML = b"<!doctype html><html><body>OpenCode</body></html>"

_lock = threading.Lock()
_n = [0]
subscribers: list[queue.Queue] = []
sessions: dict[str, "Sess"] = {}
commands_loaded = threading.Event()
mcp_read = threading.Event()


def nid(prefix: str) -> str:
    with _lock:
        _n[0] += 1
        return f"{prefix}_{_n[0]:06d}{uuid.uuid4().hex[:8]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def emit(type_: str, **data) -> None:
    line = json.dumps({"id": nid("evt"), "created": now_ms(), "type": type_,
                       "location": {"directory": os.getcwd()}, "data": data})  # fmt: skip
    with _lock:
        subs = list(subscribers)
    for q in subs:
        q.put(line)


class Aborted(Exception):
    pass


class Sess:
    def __init__(
        self, sid: str, parent: str | None = None, model: dict | None = None
    ) -> None:
        self.id, self.parent = sid, parent
        self.model = model or {"id": "fake-flash", "providerID": "opencode-go"}
        self.variant = "default"
        self.instructions: dict[str, str] = {}
        self.inbox: queue.Queue = queue.Queue()
        self.abort = threading.Event()
        self.first: str | None = None
        self.titled = False
        self.last_skills: list = []
        if parent is None:
            threading.Thread(target=worker, args=(self,), daemon=True).start()

    def info(self) -> dict:
        d = {"id": self.id, "projectID": "fake-project",
             "model": {**self.model, "variant": self.variant},
             "time": {"created": now_ms(), "updated": now_ms()},
             "location": {"directory": os.getcwd()}}  # fmt: skip
        if self.parent:
            d["parentID"] = self.parent
        return d

    def created(self) -> None:
        extra = {"parentID": self.parent} if self.parent else {}
        emit("session.created", sessionID=self.id, slug="fake-slug", version=VERSION,
             projectID="fake-project", location={"directory": os.getcwd()},
             model=self.model, **extra)  # fmt: skip


def prompts_file(sid: str) -> str:
    return os.path.join(HOME, f"{sid}.prompts")


def take_prompt(s: Sess, text: str) -> None:
    with open(prompts_file(s.id), "a") as f:
        f.write(json.dumps(text) + "\n")
    iid = nid("msg")
    emit("session.inbox.enqueued", sessionID=s.id, inboxID=iid,
         item={"type": "user", "payload": {"text": text}, "delivery": "steer"})  # fmt: skip
    s.inbox.put((iid, text))


class Step:
    def __init__(self, s: Sess) -> None:
        self.s, self.id = s, nid("msg")
        self.ordinal = 0
        emit("session.step.started", sessionID=s.id, agent="build", assistantMessageID=self.id,
             model={**s.model, "variant": s.variant}, started=now_ms())  # fmt: skip

    def text(
        self, text: str, pace: float = 0.0, chunks: list[str] | None = None
    ) -> None:
        a = {
            "sessionID": self.s.id,
            "assistantMessageID": self.id,
            "ordinal": self.ordinal,
        }
        self.ordinal += 1
        emit("session.text.started", **a)
        if chunks is None:
            k = max(1, len(text) // 3)
            chunks = [text[:k], text[k : 2 * k], text[2 * k :]]
        for c in chunks:
            emit("session.text.delta", **a, delta=c)
            if pace and self.s.abort.wait(pace):
                raise Aborted
        emit("session.text.ended", **a, text=text)

    def end(self, finish: str, failed: bool = False) -> None:
        a = {"sessionID": self.s.id, "assistantMessageID": self.id}
        if failed:
            emit("session.step.failed", **a, error={"type": "aborted", "message": "Step interrupted"},
                 cost=0, tokens=TOKENS)  # fmt: skip
            return
        emit("session.step.ended", **a, finish=finish, cost=COST, tokens=TOKENS)
        emit("session.usage.updated", sessionID=self.s.id, cost=COST, tokens=TOKENS)

    def tool_start(self, name: str, inp: dict) -> str:
        a = {"sessionID": self.s.id, "assistantMessageID": self.id}
        call = nid("call")
        emit("session.tool.input.started", **a, id=call, name=name)
        emit("session.tool.input.ended", **a, id=call, text=json.dumps(inp))
        emit("session.tool.called", **a, id=call, input=inp, executed=False)
        return call

    def tool_end(self, call: str, output: str | None = None, error: str | None = None,
                 kind: str = "tool.execution") -> None:  # fmt: skip
        a = {"sessionID": self.s.id, "assistantMessageID": self.id, "id": call}
        if error is not None:
            emit(
                "session.tool.failed",
                **a,
                error={"type": kind, "message": error},
                executed=False,
            )
        else:
            emit("session.tool.success", **a, content=[{"type": "text", "text": output or ""}],
                 metadata={"truncated": False}, executed=False)  # fmt: skip

    def tool(self, name: str, inp: dict, output: str | None = None, error: str | None = None,
             wait: float = 0.0) -> None:  # fmt: skip
        call = self.tool_start(name, inp)
        if wait and self.s.abort.wait(wait):
            self.tool_end(call, error="Tool execution interrupted", kind="aborted")
            self.end("", failed=True)
            raise Aborted
        self.tool_end(call, output=output, error=error)


def drain(s: Sess) -> list[str]:
    out = []
    while True:
        try:
            iid, text = s.inbox.get_nowait()
        except queue.Empty:
            return out
        emit("session.inbox.delivered", sessionID=s.id, inboxID=iid)
        out.append(text)


def mcp_call(tool: str, arguments: dict) -> tuple[bool, str]:
    cfg = ((CONFIG.get("mcp") or {}).get("servers") or {}).get("aegis")
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


def after_tool(s: Sess, st: Step, text: str) -> None:
    st.end("tool-calls")
    extra = drain(s)
    st2 = Step(s)
    st2.text(text + "".join(f"\nalso: {e}" for e in extra))
    st2.end("stop")


def run(s: Sess, text: str) -> None:
    if s.first is None:
        s.first = text
    word, _, rest = text.partition(" ")
    if word == "/stall":
        if s.abort.wait(float(rest or 1)):
            raise Aborted
    if word == "/exit":
        print("fake opencode: exiting on request", file=sys.stderr, flush=True)
        os._exit(int(rest or 1))
    st = Step(s)
    if word == "/sleep":
        st.tool("shell", {"command": f"sleep {rest}", "description": f"sleep {rest}"},
                output=f"slept {rest}\n", wait=float(rest or 1))  # fmt: skip
        return after_tool(s, st, f"done after {rest}")
    if word == "/fail":
        st.tool("shell", {"command": "false"}, error="exit code 1")
        return after_tool(s, st, "it failed")
    if word == "/bash":
        d, _, out = rest.partition(" => ")
        st.tool("shell", {"command": d, "description": d}, output=out)
        return after_tool(s, st, "ran it")
    if word == "/edit":
        st.tool("edit", {"path": rest, "oldString": "a", "newString": "b"},
                output=f"Edited {rest} (1 replacement)")  # fmt: skip
        return after_tool(s, st, "edited")
    if word == "/mcp":
        tool, _, args = rest.partition(" ")
        arguments = json.loads(args or "{}")
        ok, out = mcp_call(tool, arguments)
        st.tool(f"aegis_{tool}", arguments, output=out if ok else None,
                error=None if ok else out)  # fmt: skip
        return after_tool(s, st, "called it")
    if word == "/task":
        inp = {
            "agent": "general",
            "description": "look around",
            "prompt": "List the files.",
        }
        call = st.tool_start("subagent", inp)
        child = Sess(nid("ses"), parent=s.id, model=s.model)
        sessions[child.id] = child
        child.created()
        emit("session.tool.progress", sessionID=s.id, assistantMessageID=st.id, id=call,
             metadata={"sessionID": child.id, "status": "running"})  # fmt: skip
        emit("session.inbox.enqueued", sessionID=child.id, inboxID=nid("msg"),
             item={"type": "user", "payload": {"text": "List the files."}, "delivery": "steer"})  # fmt: skip
        emit("session.execution.started", sessionID=child.id)
        cst = Step(child)
        cst.text("child looked around")
        cst.end("stop")
        emit("session.execution.succeeded", sessionID=child.id)
        st.tool_end(call, output="child done")
        return after_tool(s, st, "the child is done")
    if word == "/stream":
        words = [f"chunk{i} " for i in range(1, int(rest or 4) + 1)]
        st.text("".join(words), pace=0.25, chunks=words)
    elif word == "/state":
        state = {"model": s.model, "variant": s.variant, "instructions": s.instructions,
                 "skills": s.last_skills}  # fmt: skip
        st.text("state: " + json.dumps(state, sort_keys=True))
    elif word == "/config":
        st.text("config: " + json.dumps(CONFIG, sort_keys=True))
    elif word == "/recall":
        with open(prompts_file(s.id)) as f:
            earlier = [json.loads(x) for x in f.read().splitlines()][:-1]
        st.text("earlier: " + json.dumps(earlier))
    else:
        st.text(f"you said: {text}")
    st.end("stop")


def worker(s: Sess) -> None:
    while True:
        iid, text = s.inbox.get()
        s.abort.clear()
        emit("session.execution.started", sessionID=s.id)
        emit("session.inbox.delivered", sessionID=s.id, inboxID=iid)
        model = f"{s.model['providerID']}/{s.model['id']}"
        if model in EXHAUSTED or text == "OVERFLOW":
            drain(s)
            error = OVERFLOW if text == "OVERFLOW" else LIMIT
            emit("session.execution.failed", sessionID=s.id, error=error)
            continue
        try:
            while True:
                run(s, text)
                try:
                    iid, text = s.inbox.get_nowait()
                except queue.Empty:
                    break
                emit("session.inbox.delivered", sessionID=s.id, inboxID=iid)
        except Aborted:
            drain(s)
            emit("session.execution.interrupted", sessionID=s.id, reason="user")
            continue
        emit("session.execution.succeeded", sessionID=s.id)
        if not s.titled and s.first:
            s.titled = True
            emit("session.renamed", sessionID=s.id, title=f"Fake title: {s.first[:40]}")


def new_session(body: dict) -> Sess:
    m = body.get("model") or None
    s = Sess(
        nid("ses"), model={"id": m["id"], "providerID": m["providerID"]} if m else None
    )
    sessions[s.id] = s
    open(prompts_file(s.id), "a").close()  # OpenCode keeps a session from creation
    s.created()
    emit("plugin.updated")  # noise aegis drops
    return s


def find(sid: str) -> Sess | None:
    s = sessions.get(sid)
    if s is None and os.path.exists(prompts_file(sid)):
        s = sessions[sid] = Sess(sid)
    return s


def load_commands() -> None:
    time.sleep(0.3)
    commands_loaded.set()
    emit("command.updated")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _json(self, code: int, obj, wrap: bool = True) -> None:
        if obj is not None and wrap:
            obj = {"location": {"directory": os.getcwd()}, "data": obj}
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        if data:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def _authed(self) -> bool:
        if not PASSWORD:
            return True
        want = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        if self.headers.get("authorization") == want:
            return True
        self._json(401, {"_tag": "Unauthorized"}, wrap=False)
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        if LOG:
            with open(LOG, "a") as f:
                f.write(f"{method} {path}\n")
        if not self._authed():
            return
        if not path.startswith("/api/"):
            return self._html()  # the web app, as OpenCode 2 serves it
        if method == "GET" and path == "/api/event":
            return self._events()
        if method == "GET" and path == "/api/command":
            if not commands_loaded.is_set():
                threading.Thread(target=load_commands, daemon=True).start()
                return self._json(200, [])
            return self._json(200, COMMANDS)
        if method == "GET" and path == "/api/mcp":
            # Pending at the first read, as OpenCode connects while it loads.
            if not mcp_read.is_set():
                mcp_read.set()
                status = "pending"
            else:
                status = "connected"
            servers = (CONFIG.get("mcp") or {}).get("servers") or {}
            return self._json(
                200, [{"name": n, "status": {"status": status}} for n in servers]
            )
        if method == "GET" and path == "/api/skill":
            return self._json(200, SKILLS)
        if method == "GET" and path == "/api/model":
            return self._json(200, MODELS)
        if method == "POST" and path == "/api/session":
            body = self._body()
            if os.environ.get("FAKE_OPENCODE_FAIL_SESSION"):
                return self._json(500, {"_tag": "UnknownError"}, wrap=False)
            return self._json(200, new_session(body).info())
        parts = path.strip("/").split("/")  # api, [experimental,] session, id, ...
        if parts[1:2] == ["experimental"]:
            parts = parts[:1] + parts[2:]
        if len(parts) >= 3 and parts[1] == "session":
            s = find(parts[2])
            if s is None:
                return self._json(404, {"_tag": "SessionNotFoundError"}, wrap=False)
            rest = parts[3:]
            if method == "GET" and not rest:
                return self._json(200, s.info())
            body = self._body()
            if (
                method == "PUT"
                and rest[:2] == ["instructions", "entries"]
                and len(rest) == 3
            ):
                s.instructions[rest[2]] = body.get("value")
                return self._json(204, None)
            if method != "POST":
                return self._json(404, {"_tag": "NotFound"}, wrap=False)
            if rest == ["prompt"]:
                text = body.get("text", "")
                if text == "FAIL500":
                    return self._json(500, {"_tag": "UnknownError"}, wrap=False)
                s.last_skills = body.get("skills") or []
                take_prompt(s, text)
                return self._json(200, {"id": nid("msg"), "type": "user"})
            if rest == ["model"]:
                m = body["model"]
                s.model = {"id": m["id"], "providerID": m["providerID"]}
                s.variant = m.get("variant") or "default"
                emit("session.model.selected", sessionID=s.id,
                     model={**s.model, "variant": s.variant})  # fmt: skip
                return self._json(204, None)
            if rest == ["interrupt"]:
                s.abort.set()
                return self._json(200, {"interrupted": True})
            if rest == ["command"]:
                name = body.get("name")
                if name not in {c["name"] for c in COMMANDS}:
                    return self._json(404, {"_tag": "CommandNotFoundError", "command": name,
                                            "message": f"Command not found: {name}"}, wrap=False)  # fmt: skip
                args = body.get("text", "")
                if name in SCRIPTS:
                    text = f"/{name} {args}".strip()
                else:
                    text = TEMPLATES.get(name, f"Run {name}.").replace(
                        "$ARGUMENTS", args
                    )
                take_prompt(s, text)
                return self._json(204, None)
        return self._json(404, {"_tag": "NotFound"}, wrap=False)

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
                b'data: {"id":"evt_0","type":"server.connected","data":{}}\n\n'
            )
            self.wfile.flush()
            while True:
                try:
                    line = q.get(timeout=10)
                except queue.Empty:
                    line = json.dumps(
                        {"id": nid("evt"), "type": "server.heartbeat", "data": {}}
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
    if LOG:
        with open(LOG, "a") as f:
            f.write(f"START {os.getpid()}\n")
    port = int(args[args.index("--port") + 1]) if "--port" in args else 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(f"server listening on http://127.0.0.1:{srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
