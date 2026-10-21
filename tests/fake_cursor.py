"""A stand-in for ``cursor-agent acp``, for tests and the bench.

It speaks ACP (JSON-RPC on stdio) in the shapes cursor-agent 2026.10.14
answers (spec ``2026-10-19-aegis-2-cursor-harness-design.md``, fixture
``tests/fixtures/cursor/acp-turn.jsonl``). A plain prompt is answered
"you said: <prompt>". The first word of a prompt runs a script:

    sleep N        a Bash call left running for N seconds, then text. An
                   interrupt kills the fake mid-call, so the call never
                   completes.
    stream N       N single-word chunks, one every 0.15 s, then text.
    bash D => OUT  a Bash call of D whose output is OUT.
    edit P         an edit call of P completed with a diff.
    ask            a ``session/request_permission`` for a shell command,
                   waited for: the optionId the client picked comes back as
                   text, and "no-answer" when nothing answers within 10 s
                   (the real one hangs the turn forever).
    mcp T JSON     call tool T of the aegis MCP server the session was
                   opened with, its headers included.
    servers        text "servers: <the mcpServers JSON it was opened with>".
    argv           text "argv: <JSON of argv>".
    env            text "env: <AEGIS_SESSION_TOKEN or none>".
    model          text "model: <modelId> mode: <modeId>".
    recall         text listing the prompts this session received before.
    big N          a Bash output of N kilobytes on one line.
    title T        a ``session_info_update`` with title T.
    exit N         a stderr line, then exit with code N before answering
                   the prompt, as a child that dies under a send does.

``session/new`` answers three models (``fake-pro``, ``fake-flash``, and
``fake-reason[thinking=true,effort=high]``, the one carrying an effort), two
modes and the commands ``sleep`` and ``stream``; ``session/set_model`` accepts
only those modelIds and refuses anything else with
``-32602 Invalid model value``, as the real one does. ``session/load``
reopens a session ``FAKE_CURSOR_HOME`` holds (its prompts, model and mode);
an unknown id, or ``FAKE_CURSOR_FAIL_LOAD=1``, answers an error, so the
driver falls back to a new session.

``FAKE_CURSOR_LOG=<file>`` gets ``START <pid>`` and one line per request
method. ``FAKE_CURSOR_HANG=1`` ignores a closed stdin, so only a signal ends
it. ``AEGIS_FAKE_HARNESS_VERSION`` overrides ``--version``, shared with the
other fakes.
"""

from __future__ import annotations

import sys

if sys.argv[1:2] == ["--version"]:
    # First, before the imports below: aegis reads this before every launch
    # on a new binary (harness_versions.py). The tested max, or
    # $AEGIS_FAKE_HARNESS_VERSION; empty prints nothing and exits 2.
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
            _v = tomllib.load(_f)["cursor"]["max"]
    if not _v:
        sys.exit(2)
    print("cursor-agent {}".format(_v))
    sys.exit(0)

import json
import os
import threading
import time
import urllib.request
import uuid

HOME = os.environ.get("FAKE_CURSOR_HOME") or "/tmp/fake-cursor-home"
LOG = os.environ.get("FAKE_CURSOR_LOG")
MODELS = [
    {"modelId": "fake-pro", "name": "Fake Pro", "description": "The pro model."},
    {"modelId": "fake-flash", "name": "Fake Flash", "description": "Fast."},
    {
        "modelId": "fake-reason[thinking=true,effort=high]",
        "name": "Fake Reason",
        "description": "Carries a high effort.",
    },
]
MODES = [
    {"modeId": "plan", "name": "Plan"},
    {"modeId": "agent", "name": "Agent"},
]
COMMANDS = [
    {"name": "sleep", "description": "Sleep for N seconds."},
    {"name": "stream", "description": "Stream N chunks."},
]
SCRIPTS = (
    "sleep",
    "stream",
    "bash",
    "edit",
    "ask",
    "mcp",
    "servers",
    "argv",
    "env",
    "model",
    "recall",
    "big",
    "title",
    "exit",
)
OUT = threading.Lock()
COND = threading.Condition()
RESPONSES: dict[int, dict] = {}
REQ = [900]  # our own request ids: the directions keep them clear of the client's
ASYNC = object()  # handle() answered by a thread, not now


class Refused(Exception):
    def __init__(self, message: str, code: int = -32602) -> None:
        super().__init__(message)
        self.code = code


def log(text: str) -> None:
    if LOG:
        with open(LOG, "a") as f:
            f.write(text + "\n")


def send(obj: dict) -> None:
    with OUT:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


CHUNKS = {"user_message_chunk", "agent_thought_chunk", "agent_message_chunk"}


def update(sid: str, **kw) -> None:
    send({"method": "session/update", "params": {"sessionId": sid, "update": kw}})
    if kw.get("sessionUpdate") in CHUNKS:
        # What a real load re-announces later (measured: whole-text chunks of
        # the conversation); saved so replay can send them verbatim.
        st = read(sid)
        if st is not None:
            st.setdefault("updates", []).append(kw)
            save(sid, st)


def say(sid: str, text: str) -> None:
    words = text.split(" ") or [text]
    n = max(1, len(words))
    for i, w in enumerate(words):
        update(
            sid,
            sessionUpdate="agent_message_chunk",
            content={"type": "text", "text": f"{w}{' ' if i < n - 1 else ''}"},
        )


def tool(
    sid: str,
    kind: str,
    title: str,
    raw: dict,
    content: list | None = None,
    tid: str | None = None,
    status: str = "completed",
    raw_out: dict | None = None,
) -> str:
    """A call and its completion, as cursor-agent's presentCompletedToolCall
    sends them: ``content`` for a write (the diff), ``rawOutput``
    ``{exitCode, stdout, stderr}`` for a shell's result."""
    tid = tid or uuid.uuid4().hex
    update(
        sid,
        sessionUpdate="tool_call",
        toolCallId=tid,
        title=title,
        kind=kind,
        status="pending",
        rawInput=raw,
    )
    update(
        sid,
        sessionUpdate="tool_call_update",
        toolCallId=tid,
        title=title,
        kind=kind,
        status=status,
        content=content or [],
        **({"rawOutput": raw_out} if raw_out is not None else {}),
    )
    return tid


# -- sessions ----------------------------------------------------------------
def path_of(sid: str) -> str:
    return os.path.join(HOME, f"{sid}.json")


def read(sid: str) -> dict | None:
    try:
        with open(path_of(sid)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(sid: str, st: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    with open(path_of(sid), "w") as f:
        json.dump(st, f)


def answer(sid: str, st: dict) -> dict:
    return {
        "sessionId": sid,
        "currentModelId": st["model"],
        "modes": {"availableModes": MODES, "currentModeId": st["mode"]},
        "models": {"availableModels": MODELS, "currentModelId": st["model"]},
    }


def announce_commands(sid: str) -> None:
    update(
        sid,
        sessionUpdate="available_commands_update",
        availableCommands=COMMANDS,
    )


def open_session(params: dict, sid: str | None) -> dict:
    st = None if sid is None else read(sid)
    if st is None:
        sid = sid or uuid.uuid4().hex
        st = {"model": "fake-pro", "mode": "agent", "prompts": []}
    st["mcp"] = params.get("mcpServers") or []
    save(sid, st)
    # Before the answer, as an eager server would: the client builds its
    # catalog from the session's first lines, commands included.
    announce_commands(sid)
    return answer(sid, st)


def need(p: dict) -> tuple[str, dict]:
    sid = str(p.get("sessionId") or "")
    st = read(sid)
    if st is None:
        raise Refused(f"unknown session {sid}")
    return sid, st


# -- a permission the client must answer ------------------------------------
def ask(sid: str, kind: str, title: str, raw: dict) -> str:
    REQ[0] += 1
    rid = REQ[0]
    send(
        {
            "id": rid,
            "method": "session/request_permission",
            "params": {
                "sessionId": sid,
                "toolCall": {
                    "toolCallId": uuid.uuid4().hex,
                    "title": title,
                    "kind": kind,
                    "rawInput": raw,
                },
                "options": [
                    {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
                    {
                        "optionId": "allow-always",
                        "name": "Always allow",
                        "kind": "allow_always",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                    {
                        "optionId": "reject-always",
                        "name": "Never allow",
                        "kind": "reject_always",
                    },
                ],
            },
        }
    )
    with COND:
        end = time.time() + 10
        while rid not in RESPONSES and time.time() < end:
            COND.wait(0.2)
        msg = RESPONSES.pop(rid, None)
    if msg is None:
        return "no-answer"
    outcome = (msg.get("result") or {}).get("outcome") or {}
    if outcome.get("outcome") == "selected":
        return str(outcome.get("optionId"))
    return str(outcome.get("outcome") or "cancelled")


# -- the aegis MCP server the session was opened with -----------------------
def mcp_call(tool_name: str, arguments: dict, servers: list) -> tuple[bool, str]:
    srv = next((s for s in servers if s.get("name") == "aegis"), None)
    if srv is None or not srv.get("url"):
        return False, "no aegis MCP server configured"
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
    )
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    for h in srv.get("headers") or []:
        headers[str(h.get("name"))] = str(h.get("value"))
    req = urllib.request.Request(srv["url"], data=body.encode(), headers=headers)
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


# -- one turn ----------------------------------------------------------------
def turn(rid: int, p: dict) -> None:
    sid = str(p.get("sessionId") or "")
    st = read(sid) or {}
    text = " ".join(str(x.get("text") or "") for x in p.get("prompt") or []).strip()
    st.setdefault("prompts", []).append(text.replace("\n", " "))
    save(sid, st)
    word, _, args = text.partition(" ")
    if word.startswith("/"):
        word = word[1:]  # aegis sends a slash line as written; scripts are bare
    if word == "exit":
        sys.stderr.write("fake cursor is exiting\n")
        sys.stderr.flush()
        os._exit(int(args or 1))
    if word == "stream":
        for i in range(int(args or 3)):
            update(
                sid,
                sessionUpdate="agent_message_chunk",
                content={"type": "text", "text": f"chunk{i + 1} "},
            )
            time.sleep(0.15)
        say(sid, f"streamed {int(args or 3)}")
    elif word == "sleep":
        tid = tool(
            sid,
            "execute",
            f"Run sleep {args or 1}",
            {"command": f"sleep {args or 1}"},
            status="in_progress",
        )
        time.sleep(float(args or 1))
        update(
            sid,
            sessionUpdate="tool_call_update",
            toolCallId=tid,
            title=f"Run sleep {args or 1}",
            kind="execute",
            status="completed",
            content=[],
            rawOutput={"exitCode": 0, "stdout": "", "stderr": ""},
        )
        say(sid, "slept")
    elif word == "bash":
        cmd, _, out = args.partition(" => ")
        tool(
            sid,
            "execute",
            f"Run {cmd}",
            {"command": cmd},
            raw_out={"exitCode": 0, "stdout": out, "stderr": ""},
        )
        say(sid, "ran it")
    elif word == "edit":
        tool(
            sid,
            "edit",
            f"Edit {args}",
            {"path": args},
            [{"type": "diff", "path": args, "oldText": "a", "newText": "b"}],
        )
        say(sid, "edited")
    elif word == "ask":
        kind = args or "execute"
        title = "Run rm -rf /" if kind == "execute" else "Edit notes.md"
        got = ask(
            sid,
            kind,
            title,
            {"command": "rm -rf /"} if kind == "execute" else {"path": "notes.md"},
        )
        say(sid, f"permission {kind}: {got}")
    elif word == "mcp":
        name, _, raw = args.partition(" ")
        ok, out = mcp_call(name, json.loads(raw or "{}"), st.get("mcp") or [])
        say(sid, f"mcp {'ok' if ok else 'failed'}: {out}")
    elif word == "servers":
        say(sid, "servers: " + json.dumps(st.get("mcp") or []))
    elif word == "argv":
        say(sid, "argv: " + json.dumps(sys.argv[1:]))
    elif word == "env":
        say(sid, "env: " + os.environ.get("AEGIS_SESSION_TOKEN", "none"))
    elif word == "model":
        say(sid, f"model: {st.get('model')} mode: {st.get('mode')}")
    elif word == "recall":
        say(sid, "recall: " + " | ".join(st.get("prompts", [])[:-1]))
    elif word == "big":
        tool(
            sid,
            "execute",
            "Run big",
            {"command": "cat big"},
            raw_out={
                "exitCode": 0,
                "stdout": "x" * (int(args or 100) * 1024),
                "stderr": "",
            },
        )
        say(sid, "big done")
    elif word == "title":
        update(
            sid,
            sessionUpdate="session_info_update",
            title=args or "Untitled",
        )
        say(sid, "titled")
    else:
        say(sid, f"you said: {text}")
    send({"id": rid, "result": {"stopReason": "end_turn"}})


# -- requests ----------------------------------------------------------------
def handle(msg: dict):
    method, p = msg.get("method"), msg.get("params") or {}
    log(str(method))
    if method == "initialize":
        return {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}
    if method == "session/new":
        return open_session(p, None)
    if method == "session/load":
        if os.environ.get("FAKE_CURSOR_FAIL_LOAD"):
            raise Refused("the fake refuses to load")
        sid = str(p.get("sessionId") or "")
        st = read(sid)
        if st is None:
            raise Refused(f"unknown session {sid}")  # as the real one errors
        # Measured on the real driver: load replays the conversation as
        # full-text chunks before it answers, and its answer carries the
        # same keys as session/new minus sessionId. Raw send, not update():
        # replaying must not save the replay again.
        for text in st.get("prompts", []):
            send(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": sid,
                        "update": {
                            "sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    },
                }
            )
        for kw in st.get("updates", []):
            send(
                {"method": "session/update", "params": {"sessionId": sid, "update": kw}}
            )
        open_session(p, sid)
        return {k: v for k, v in answer(sid, st).items() if k != "sessionId"}
    if method == "session/prompt":
        threading.Thread(target=turn, args=(msg["id"], p), daemon=True).start()
        return ASYNC
    if method == "session/set_model":
        _, st = need(p)
        if p.get("modelId") not in {m["modelId"] for m in MODELS}:
            raise Refused(f"Invalid model value: {p.get('modelId')}")
        st["model"] = p["modelId"]
        save(str(p.get("sessionId")), st)
        return {}
    if method == "session/set_mode":
        _, st = need(p)
        if p.get("modeId") not in {m["modeId"] for m in MODES}:
            raise Refused(f"Invalid mode value: {p.get('modeId')}")
        st["mode"] = p["modeId"]
        save(str(p.get("sessionId")), st)
        return {}
    raise Refused(f"the fake does not know {method}", code=-32601)


def main() -> None:
    os.makedirs(HOME, exist_ok=True)
    log(f"START {os.getpid()}")
    for raw in sys.stdin:
        msg = json.loads(raw)
        if "method" not in msg:
            with COND:
                RESPONSES[msg["id"]] = msg
                COND.notify_all()
            continue  # an answer to one of our requests
        if "id" not in msg:
            continue  # notifications/initialized
        try:
            result = handle(msg)
            if result is not ASYNC:
                send({"id": msg["id"], "result": result})
        except Refused as e:
            send({"id": msg["id"], "error": {"code": e.code, "message": str(e)}})
    if os.environ.get("FAKE_CURSOR_HANG"):
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
