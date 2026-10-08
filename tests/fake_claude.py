"""A stand-in for ``claude -p`` in stream-json mode, for tests and the bench.

It reads user messages and control requests on stdin and writes stream-json on
stdout. The text of a prompt picks a script:

    /sleep N       a Bash call that takes N seconds, then text and a result.
                   Prompts that arrive meanwhile are echoed at the tool
                   boundary and answered in the same turn, as Claude does.
    /deafsleep N   the same, but it ignores interrupts.
    /fail          a Bash call that fails.
    /notice        three system notices and nothing else.
    /big           a Read whose result is 2 MB on one line.
    /exit N        a few stderr lines, then exit with code N.
    /recall        text listing the prompts this session id received before.
    /argv          text "argv: <JSON of the process's argv after the binary>".
    /mcp T JSON    call tool T of the aegis MCP server named in --mcp-config with
                   arguments JSON, as a tool call and its result, then a result.
    /bgtask N      start a background task (task_started) and end the turn; N
                   seconds later the task is notified and the fake wakes itself
                   with a second turn, as Claude does.
    /context, /model, /effort, /rename
                   run locally, as Claude does: no echo, a <synthetic>
                   assistant line with local_command_run, a zero-cost result.
    /compact       a compact_boundary, the replayed "Compacted" note, a result.
    /clear         a conversation_reset; the next turn has a new session id.
    /hello NAME    a prompt command: a <command-message> echo, then text.
    anything else  text that quotes the prompt, then a result.

An interrupt ``control_request`` ends a running script with an error result.
``initialize`` (COMMANDS and MODELS below), ``set_model``,
``apply_flag_settings``, ``get_settings`` and ``set_permission_mode`` answer as
Claude Code 2.1.283 does: an unknown model is an error, and an effort level the
current model does not list is answered with success and not applied.
``FAKE_CLAUDE_NO_INIT=1`` answers ``initialize`` with an error, as a CLI that
does not know it would; ``FAKE_CLAUDE_INIT_LOG=<file>`` gets one line per
``initialize`` received, so a test can count probes.

``--resume <id>`` keeps that session id, as Claude does; without it the fake
mints one. Each id's prompts are appended to ``$FAKE_CLAUDE_HOME/<id>.prompts``
(default: the system temp dir), which is what ``/recall`` reads, so a test can
prove a resumed process has its earlier context.

With ``FAKE_CLAUDE_REPLAY=<store file>``, the first prompt instead replays the
Claude lines of an aegis store, ``FAKE_CLAUDE_PACE`` seconds apart (default
0). ``FAKE_CLAUDE_REPEAT`` replays it that many times, with tool ids made
unique per round. ``FAKE_CLAUDE_EMIT_LOG`` gets one ``<tool id> <unix time>``
line per tool call written, for the bench.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
import urllib.request
import uuid


def _arg(flag: str) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


OUT = threading.Lock()
inbox: queue.Queue = queue.Queue()
interrupted = threading.Event()
state: dict = {
    "cost": 0.0,
    "tool": 0,
    "inited": False,
    "deaf": False,
    "model": None,  # the resolved id a set_model switched to
    "effort": _arg("--effort"),
}
SCRIPTS = (
    "sleep",
    "deafsleep",
    "fail",
    "notice",
    "big",
    "exit",
    "recall",
    "mcp",
    "bgtask",
    "argv",
    "bash",
)
COMMANDS = (
    [
        {"name": w, "description": f"Fake script {w}.", "argumentHint": ""}
        for w in SCRIPTS
    ]
    + [
        {
            "name": n,
            "description": f"Claude's own {n}.",
            "argumentHint": "",
            "builtin": True,
        }
        for n in ("compact", "clear", "context", "model", "effort", "rename")
    ]
    + [
        {
            "name": "hello",
            "description": "Say hello to someone. (project)",
            "argumentHint": "<name>",
        }
    ]
)
LEVELS = ["low", "medium", "high", "xhigh", "max"]
MODELS = [
    {
        "value": "opus",
        "resolvedModel": "fake-opus",
        "displayName": "Opus",
        "description": "The big one.",
        "supportedEffortLevels": LEVELS,
    },
    {
        "value": "sonnet",
        "resolvedModel": "fake-sonnet",
        "displayName": "Sonnet",
        "description": "The middle one.",
        "supportedEffortLevels": LEVELS,
    },
    {
        "value": "haiku",
        "resolvedModel": "fake-haiku",
        "displayName": "Haiku",
        "description": "Takes no effort level.",
    },
    {
        "value": "claude-fake-old",
        "resolvedModel": "claude-fake-old",
        "displayName": "Fake Old",
        "description": "Has no xhigh.",
        "supportedEffortLevels": ["low", "medium", "high", "max"],
    },
    {
        "value": "retired",
        "resolvedModel": "fake-retired",
        "displayName": "Retired",
        "description": "Update to use it.",
        "disabled": True,
    },
]
SESSION_ID = (
    sys.argv[sys.argv.index("--resume") + 1]
    if "--resume" in sys.argv
    else str(uuid.uuid4())
)
HOME = os.environ.get("FAKE_CLAUDE_HOME") or tempfile.gettempdir()


def mcp_call(tool: str, arguments: dict) -> tuple[bool, str]:
    """POST one tools/call to the server and header in --mcp-config."""
    try:
        cfg = json.loads(sys.argv[sys.argv.index("--mcp-config") + 1])["mcpServers"][
            "aegis"
        ]
    except (ValueError, KeyError, IndexError):
        return False, "no aegis MCP server configured"
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    req = urllib.request.Request(
        cfg["url"],
        data=body.encode(),
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            **cfg.get("headers", {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            reply = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if "error" in reply:
        return False, json.dumps(reply["error"])
    res = reply["result"]
    text = "".join(c.get("text", "") for c in res.get("content", []))
    return not res.get("isError"), text


def prompts_file() -> str:
    return os.path.join(HOME, f"{SESSION_ID}.prompts")


def earlier_prompts() -> list[str]:
    try:
        with open(prompts_file()) as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def emit(obj: dict) -> None:
    with OUT:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def echo(text: str) -> None:
    emit(
        {"type": "user", "isReplay": True, "message": {"role": "user", "content": text}}
    )


def assistant(*blocks: dict) -> None:
    emit(
        {
            "type": "assistant",
            "message": {
                "content": list(blocks),
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 1000,
                    "output_tokens": 20,
                },
            },
        }
    )


def result(
    is_error: bool = False, subtype: str = "success", cost: float = 0.01, turns: int = 1
) -> None:
    state["cost"] += cost
    emit(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "duration_ms": 100,
            "total_cost_usd": round(state["cost"], 4),
            "stop_reason": None if is_error else "end_turn",
            "modelUsage": {"fake-model": {"contextWindow": 200000}},
            "num_turns": turns,
        }
    )


def local_output(text: str) -> None:
    emit(
        {
            "type": "user",
            "isReplay": True,
            "message": {
                "role": "user",
                "content": f"<local-command-stdout>{text}</local-command-stdout>",
            },
        }
    )


def _model(name: str | None) -> dict | None:
    return next(
        (
            m
            for m in MODELS
            if name in (m["value"], m["resolvedModel"]) and not m.get("disabled")
        ),
        None,
    )


def control(msg: dict) -> None:
    req = msg.get("request") or {}
    sub, rid = req.get("subtype"), msg.get("request_id")
    body: dict = {}
    error = None
    if sub == "interrupt":
        interrupted.set()
    elif sub == "initialize":
        if os.environ.get("FAKE_CLAUDE_INIT_LOG"):
            with open(os.environ["FAKE_CLAUDE_INIT_LOG"], "a") as f:
                f.write(f"{os.getpid()}\n")
        if os.environ.get("FAKE_CLAUDE_NO_INIT"):
            error = "unknown subtype: initialize"
        else:
            body = {"commands": COMMANDS, "models": MODELS}
    elif sub == "set_model":
        m = _model(req.get("model"))
        if m is None:
            error = f"Model '{req.get('model')}' not found"
        else:
            state["model"], state["inited"] = m["resolvedModel"], False
            local_output(f"Set model to {m['displayName']}")
    elif sub == "apply_flag_settings":
        level = (req.get("settings") or {}).get("effortLevel")
        current = _model(state["model"] or _arg("--model")) or MODELS[0]
        if level in current.get("supportedEffortLevels", []):
            state["effort"] = level
    elif sub == "get_settings":
        body = {"applied": {"model": state["model"], "effort": state["effort"]}}
    elif sub == "set_permission_mode":
        body = {"mode": req.get("mode")}
    resp = (
        {"request_id": rid, "subtype": "error", "error": error}
        if error
        else {"request_id": rid, "subtype": "success", "response": body}
    )
    emit({"type": "control_response", "response": resp})


def tool_id() -> str:
    state["tool"] += 1
    return f"toolu_fake_{state['tool']}"


def tool_output(tid: str, text: str, is_error: bool = False) -> None:
    emit(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": text,
                        "is_error": is_error,
                    }
                ]
            },
        }
    )


def wait(seconds: float) -> bool:
    """Sleep; True when an interrupt cut it short."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if interrupted.is_set() and not state["deaf"]:
            return True
        time.sleep(0.02)
    return False


def run(text: str) -> None:
    global SESSION_ID
    if not state["inited"]:
        state["inited"] = True
        emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": SESSION_ID,
                "model": state["model"] or "fake-model",
                "claude_code_version": "0.0-fake",
            }
        )
    word, _, arg = text.partition(" ")
    if word in ("/context", "/model", "/effort", "/rename"):
        emit(
            {
                "type": "assistant",
                "local_command_run": {"command": word[1:], "args": arg},
                "message": {
                    "model": "<synthetic>",
                    "content": [{"type": "text", "text": f"ran {text[1:]}"}],
                },
            }
        )
        result(cost=0.0, turns=0)
        return
    if word == "/compact":
        emit(
            {
                "type": "system",
                "subtype": "compact_boundary",
                "compact_metadata": {"pre_tokens": 50000, "post_tokens": 4000},
            }
        )
        local_output("Compacted ")
        result(turns=0)
        return
    if word == "/clear":
        SESSION_ID, state["inited"] = str(uuid.uuid4()), False
        emit({"type": "conversation_reset", "trigger": "clear"})
        result(cost=0.0, turns=0)
        return
    if word == "/hello":
        echo(
            "<command-message>hello</command-message>\n<command-name>/hello</command-name>\n"
            f"<command-args>{arg}</command-args>"
        )
        assistant({"type": "text", "text": f"HELLO {arg}"})
        result()
        return
    echo(text)
    before = earlier_prompts()
    with open(prompts_file(), "a") as f:
        f.write(json.dumps(text) + "\n")
    if word in ("/sleep", "/deafsleep"):
        state["deaf"] = word == "/deafsleep"
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Bash",
                "input": {"command": f"sleep {arg}", "description": "Wait"},
            }
        )
        if wait(float(arg or 1)):
            interrupted.clear()
            result(is_error=True, subtype="error_during_execution")
            return
        state["deaf"] = False
        tool_output(tid, "slept")
        last = text
        while True:
            try:
                injected = inbox.get_nowait()
            except queue.Empty:
                break
            echo(injected)
            last = injected
        assistant({"type": "text", "text": f"done: {last}"})
        result()
    elif word == "/fail":
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Bash",
                "input": {"command": "false"},
            }
        )
        tool_output(tid, "Exit code 1\nError: it failed", is_error=True)
        assistant({"type": "text", "text": "That failed."})
        result()
    elif word == "/bash":
        desc, _, out = arg.partition(" => ")
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Bash",
                "input": {"command": "true", "description": desc},
            }
        )
        tool_output(tid, out)
        result()
    elif word == "/notice":
        for sub in ("hook_started", "thinking_tokens", "task_notification"):
            emit({"type": "system", "subtype": sub})
    elif word == "/big":
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Read",
                "input": {"file_path": "/big.txt"},
            }
        )
        tool_output(tid, "\n".join(f"line {i} " + "x" * 60 for i in range(30000)))
        result()
    elif word == "/mcp":
        tool, _, raw = arg.partition(" ")
        tid = tool_id()
        args = json.loads(raw or "{}")
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": f"mcp__aegis__{tool}",
                "input": args,
            }
        )
        ok, text = mcp_call(tool, args)
        tool_output(tid, text, is_error=not ok)
        assistant({"type": "text", "text": f"mcp {'ok' if ok else 'error'}: {text}"})
        result()
    elif word == "/bgtask":
        task_id = f"bg{state['tool']}"
        emit(
            {
                "type": "system",
                "subtype": "task_started",
                "task_id": task_id,
                "is_backgrounded": True,
            }
        )
        assistant({"type": "text", "text": "started a background task"})
        result()

        def finish():
            time.sleep(float(arg or 1))
            emit(
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "task_id": task_id,
                    "status": "completed",
                }
            )
            assistant({"type": "text", "text": "the background task finished"})
            result()

        threading.Thread(target=finish, daemon=True).start()
    elif word == "/argv":
        assistant({"type": "text", "text": "argv: " + json.dumps(sys.argv[1:])})
        result()
    elif word == "/recall":
        assistant({"type": "text", "text": "earlier: " + " | ".join(before)})
        result()
    elif word == "/exit":
        sys.stderr.write("fatal: something broke\nsecond line\n")
        sys.stderr.flush()
        os._exit(int(arg or 1))
    else:
        assistant({"type": "text", "text": f"You said: **{text}**"})
        result()


def replay(path: str) -> None:
    pace = float(os.environ.get("FAKE_CLAUDE_PACE", "0"))
    repeat = int(os.environ.get("FAKE_CLAUDE_REPEAT", "1"))
    log_path = os.environ.get("FAKE_CLAUDE_EMIT_LOG")
    log = open(log_path, "a") if log_path else None
    lines = []
    with open(path) as f:
        for raw in f:
            rec = json.loads(raw)
            if rec.get("src") == "claude":
                lines.append(rec["line"])
    for r in range(repeat):
        for line in lines:
            if r:
                line = line.replace('"toolu_', f'"toolu_r{r}_')
            emit(json.loads(line))
            if log and '"tool_use"' in line:
                for block in json.loads(line).get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        log.write(f"{block['id']} {time.time()}\n")
                log.flush()
            if pace:
                time.sleep(pace)


def worker() -> None:
    replayed = False
    while True:
        text = inbox.get()
        if os.environ.get("FAKE_CLAUDE_REPLAY") and not replayed:
            replayed = True
            replay(os.environ["FAKE_CLAUDE_REPLAY"])
            continue
        run(text)


def main() -> None:
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("type") == "control_request":
            control(msg)
        elif msg.get("type") == "user":
            interrupted.clear()
            inbox.put(msg["message"]["content"])
    # stdin closed: leave like claude does
    time.sleep(0.05)


if __name__ == "__main__":
    main()
