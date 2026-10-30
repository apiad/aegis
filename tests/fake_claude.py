"""A stand-in for ``claude -p`` in stream-json mode, for tests and the bench.

It reads user messages and control requests on stdin and writes stream-json on
stdout. The text of a prompt picks a script:

    /busy N        a Bash call that takes N seconds, then text and a result.
                   Prompts that arrive meanwhile are echoed at the tool
                   boundary and answered in the same turn, as Claude does.
    /deafsleep N   the same, but it ignores interrupts.
    /fail          a Bash call that fails.
    /bash D => OUT a Bash call described D whose output is OUT, then a result.
    /many N        N Bash calls in one turn, each with its output, then a
                   result: N rows, for a test that needs a long transcript.
    /ctx N [miss|dup]  one reply at context N; miss writes the cache instead
                   of reading it, dup repeats the usage on a tool line.
    [[slow]]       anywhere in a prompt: wait 1.5 s before answering.
    /md TEXT       TEXT as the reply, each literal \\n a newline, then a result.
    /notice        three system notices and nothing else.
    /big           a Read whose result is 2 MB on one line.
    /exit N        a few stderr lines, then exit with code N.
    /quit N        a reply and a result, then N seconds later exit with code 0,
                   between turns.
    /recall        text listing the prompts this session id received before.
    /stream N      N words of text, one every 0.25 s, then a result. With
                   --include-partial-messages each word is a stream_event delta
                   as Claude 2.1.291 sends it; without, the text arrives whole.
    /argv          text "argv: <JSON of the process's argv after the binary>".
    /mcp T JSON    call tool T of the aegis MCP server named in --mcp-config with
                   arguments JSON, as a tool call and its result, then a result.
    /script JSON   a list of steps in one turn: ["sleep", N] waits N seconds
                   (an interrupt ends the turn, as /busy's does); ["say", TEXT]
                   writes TEXT as its own text block; [T, {args}]
                   calls aegis tool T as /mcp does. Then text naming each
                   call's outcome, and a result.
    /bgtask N      start a background task (task_started) and end the turn; N
                   seconds later the task is notified and the fake wakes itself
                   with a second turn, as Claude does.
    /context, /model, /effort, /rename
                   run locally, as Claude does: no echo, a <synthetic>
                   assistant line with local_command_run, a zero-cost result.
    /compact       a compact_boundary, the replayed "Compacted" note, a result.
    /clear         a conversation_reset; the next turn has a new session id.
    /hello NAME    a prompt command: a <command-message> echo, then text.
    /sh CMD        a Bash call that runs CMD with shell=True; output is
                   stdout+stderr; a non-zero exit is an error result reading
                   "Exit code N".
    /write PATH => TEXT
                   a Write call that writes TEXT to PATH.
    /edit PATH OLD => NEW
                   an Edit call that replaces the first OLD with NEW in PATH.
    anything else  text that quotes the prompt, then a result.

An interrupt ``control_request`` ends a running script with an error result.
``initialize`` (COMMANDS and MODELS below), ``set_model``,
``apply_flag_settings``, ``get_settings`` and ``set_permission_mode`` answer as
Claude Code 2.1.283 does: an unknown model is an error, and an effort level the
current model does not list is answered with success and not applied.
``FAKE_CLAUDE_NO_INIT=1`` answers ``initialize`` with an error, as a CLI that
does not know it would; ``FAKE_CLAUDE_EXTRA_MODEL=<id>`` adds one model to
MODELS, one no config names; ``FAKE_CLAUDE_INIT_LOG=<file>`` gets one line per
``initialize`` received, so a test can count probes.

``--resume <id>`` keeps that session id, as Claude does; without it the fake
mints one. Each id's prompts are appended to ``$FAKE_CLAUDE_HOME/<id>.prompts``
(default: the system temp dir), which is what ``/recall`` reads, so a test can
prove a resumed process has its earlier context.

``-p PROMPT`` one-shot (``--output-format json``, which a session's ``-p``
never has): prints a JSON envelope whose structured output is a recap built
from the prompt; ``FAKE_CLAUDE_ONESHOT=fail|garbage|empty|slow`` makes it exit
1, print non-JSON, print an envelope with a cost and no answer (after
``FAKE_CLAUDE_ONESHOT_SLEEP`` seconds, when set), or sleep 120 s
(``FAKE_CLAUDE_ONESHOT_SLEEP`` seconds, when set) before answering. Each one-shot appends a line to
``$FAKE_CLAUDE_HOME/oneshot.log`` and writes its pid to
``$FAKE_CLAUDE_HOME/oneshot.pid``, so a test can count calls and find the child.
Without ``--json-schema`` it is the sign-in check (claude/login.py): it logs
its ``CLAUDE_CONFIG_DIR`` to ``$FAKE_CLAUDE_HOME/probe.log`` and answers ok.

A ``CLAUDE_CODE_OAUTH_TOKEN`` starting ``sk-ant-oat01-bad`` is refused: a turn
ends with Claude Code's two 401 lines, the check with an error result.
``FAKE_CLAUDE_RATE_LIMIT=<fraction>`` adds a ``rate_limit_event`` to each turn,
the five-hour window at that utilization.

A ``--json-schema`` with an ``answer`` property is a side note: it gets
``{"answer": "btw of: <question>"}``, and ``needs_more`` when the question
carries ``[[more]]``.

With ``FAKE_CLAUDE_REPLAY=<store file>``, the first prompt instead replays the
Claude lines of an aegis store, ``FAKE_CLAUDE_PACE`` seconds apart (default
0). ``FAKE_CLAUDE_REPEAT`` replays it that many times, with tool ids made
unique per round. ``FAKE_CLAUDE_EMIT_LOG`` gets one ``<tool id> <unix time>``
line per tool call written, for the bench.
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
            _v = tomllib.load(_f)["claude-code"]["max"]
    if not _v:
        sys.exit(2)
    print("{} (Claude Code)".format(_v))
    sys.exit(0)

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path


def _arg(flag: str) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


OUT = threading.Lock()
inbox: queue.Queue = queue.Queue()
NOTICE_HEADER = "> from aegis:notice · "
STEER_WAIT_S = 0.03
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
    "busy",
    "deafsleep",
    "fail",
    "notice",
    "big",
    "exit",
    "quit",
    "recall",
    "mcp",
    "bgtask",
    "argv",
    "bash",
    "md",
    "split",
    "read",
    "readn",
    "stream",
    "sh",
    "write",
    "edit",
    "many",
    "ctx",
    "script",
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
        "value": "retired",
        "resolvedModel": "fake-retired",
        "displayName": "Retired",
        "description": "Update to use it.",
        "disabled": True,
    },
]
if os.environ.get("FAKE_CLAUDE_EXTRA_MODEL"):
    _extra = os.environ["FAKE_CLAUDE_EXTRA_MODEL"]
    MODELS.append(
        {
            "value": _extra,
            "resolvedModel": _extra,
            "displayName": _extra,
            "description": "In no config.",
            "supportedEffortLevels": LEVELS,
        }
    )
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
            # Claude Code expands ${VAR} in --mcp-config from its environment.
            **{
                k: re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m[1], ""), v)
                for k, v in cfg.get("headers", {}).items()
            },
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


def stream_text(text: str) -> None:
    """One text block, word by word, as Claude streams it with partial
    messages: the assistant line lands before the block's stop."""
    partial = "--include-partial-messages" in sys.argv
    mid = f"msg_{uuid.uuid4().hex}"

    def event(ev: dict) -> None:
        if partial:
            emit({"type": "stream_event", "event": ev, "parent_tool_use_id": None})

    event({"type": "message_start", "message": {"id": mid, "content": []}})
    event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
    )
    for k, word in enumerate(text.split(" ")):
        delta = {"type": "text_delta", "text": word if k == 0 else " " + word}
        event({"type": "content_block_delta", "index": 0, "delta": delta})
        time.sleep(0.25)
    emit(
        {
            "type": "assistant",
            "message": {
                "id": mid,
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        }
    )
    event({"type": "content_block_stop", "index": 0})
    event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
    event({"type": "message_stop"})


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


def usage_line(blocks: list[dict], usage: dict) -> None:
    emit({"type": "assistant", "message": {"content": blocks, "usage": usage}})


def tool_id() -> str:
    state["tool"] += 1
    return f"toolu_fake_{state['tool']}"


def read_steered_notices() -> None:
    """As the real CLI reads what reached stdin by the tool boundary and echoes
    it. A model takes seconds to ask again; this gives aegis a moment to write."""
    time.sleep(STEER_WAIT_S)
    with inbox.mutex:
        notices = [t for t in inbox.queue if str(t).startswith(NOTICE_HEADER)]
        for t in notices:
            inbox.queue.remove(t)
    for t in notices:
        echo(t)


def tool_output(tid: str, text: str, is_error: bool = False) -> None:
    read_steered_notices()
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


def bad_token() -> bool:
    """A token the fake refuses, as the real CLI refuses a revoked one."""
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").startswith("sk-ant-oat01-bad")


def probe() -> None:
    """The sign-in check (claude/login.py): one request, a JSON result."""
    sys.stdin.read()
    with open(os.path.join(HOME, "probe.log"), "a") as f:
        f.write(f"{os.environ.get('CLAUDE_CONFIG_DIR', '')}\n")
    if bad_token():
        print(
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": "Failed to authenticate. API Error: 401 Invalid bearer token",
                }
            )
        )
        sys.exit(1)
    print(json.dumps({"type": "result", "is_error": False, "result": "ok"}))


def run(text: str) -> None:
    global SESSION_ID
    if not state["inited"]:
        state["inited"] = True
        emit(
            {
                "type": "system",
                "subtype": "init",
                "cwd": os.getcwd(),
                "session_id": SESSION_ID,
                "model": state["model"] or "fake-model",
                "claude_code_version": "0.0-fake",
            }
        )
    if bad_token():
        # As Claude Code 2.1.309 does when its token is refused.
        text = "Failed to authenticate. API Error: 401 OAuth access token is invalid."
        emit(
            {
                "type": "assistant",
                "error": "authentication_failed",
                "message": {
                    "model": "<synthetic>",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "api_error_status": 401,
                "terminal_reason": "api_error",
                "result": text,
                "duration_ms": 50,
                "total_cost_usd": round(state["cost"], 4),
                "num_turns": 1,
            }
        )
        return
    if os.environ.get("FAKE_CLAUDE_RATE_LIMIT"):
        emit(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "rateLimitType": "five_hour",
                    "unifiedWindows": {
                        "five_hour": {
                            "utilization": float(os.environ["FAKE_CLAUDE_RATE_LIMIT"]),
                            "resetsAt": int(time.time()) + 3600,
                        },
                        "seven_day": {
                            "utilization": 0.04,
                            "resetsAt": int(time.time()) + 86400,
                        },
                    },
                },
            }
        )
    # A scheduled prompt's or a workflow node's command is the line under its
    # header; what follows (a check's stdout, the node's preamble) is not part
    # of it. The echo keeps the whole prompt.
    headed = text.startswith(("> from schedule:", "> from workflow:"))
    cmd = text.splitlines()[1] if headed else text
    word, _, arg = cmd.partition(" ")
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
    if "[[slow]]" in text:
        time.sleep(1.5)
    before = earlier_prompts()
    with open(prompts_file(), "a") as f:
        f.write(json.dumps(text) + "\n")
    if word in ("/busy", "/deafsleep"):
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
    elif word == "/many":
        for k in range(int(arg or 1)):
            tid = tool_id()
            assistant(
                {
                    "type": "tool_use",
                    "id": tid,
                    "name": "Bash",
                    "input": {"command": "true", "description": f"step {k}"},
                }
            )
            tool_output(tid, f"out {k}")
        result()
    elif word == "/ctx":
        n, _, how = arg.partition(" ")
        usage = {"input_tokens": 10, "output_tokens": 20}
        cached = (
            "cache_creation_input_tokens"
            if how == "miss"
            else "cache_read_input_tokens"
        )
        usage[cached] = int(n) - 30
        usage_line([{"type": "text", "text": f"ctx {n}"}], usage)
        if how == "dup":
            tid = tool_id()
            usage_line(
                [
                    {
                        "type": "tool_use",
                        "id": tid,
                        "name": "Bash",
                        "input": {"command": "true"},
                    }
                ],
                usage,
            )
            tool_output(tid, "")
        result()
    elif word == "/sh":
        tid = tool_id()
        assistant(
            {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": arg}}
        )
        r = subprocess.run(arg, shell=True, capture_output=True, text=True)
        out = (r.stdout + r.stderr).rstrip("\n")
        if r.returncode:
            tool_output(tid, f"Exit code {r.returncode}\n{out}", is_error=True)
        else:
            tool_output(tid, out)
        assistant({"type": "text", "text": "Executed command."})
        result()
    elif word == "/write":
        path, _, text = arg.partition(" => ")
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Write",
                "input": {"file_path": path, "content": text},
            }
        )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)
        tool_output(tid, f"File created successfully at: {path}")
        assistant({"type": "text", "text": "Wrote file."})
        result()
    elif word == "/edit":
        path, _, rest = arg.partition(" ")
        old, _, new = rest.partition(" => ")
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Edit",
                "input": {"file_path": path, "old_string": old, "new_string": new},
            }
        )
        body = Path(path).read_text()
        if old not in body:
            tool_output(tid, "String to replace not found in file.", is_error=True)
        else:
            Path(path).write_text(body.replace(old, new, 1))
            tool_output(tid, f"The file {path} has been updated successfully.")
        assistant({"type": "text", "text": "File edited."})
        result()
    elif word == "/md":
        assistant({"type": "text", "text": arg.replace("\\n", "\n")})
        result()
    elif word == "/split":
        # Two replies with a tool call between them: one turn's messages.
        first, _, second = arg.partition(" || ")
        assistant({"type": "text", "text": first.replace("\\n", "\n")})
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Bash",
                "input": {"command": "true", "description": "between"},
            }
        )
        tool_output(tid, "tool output nobody reads aloud")
        assistant({"type": "text", "text": second.replace("\\n", "\n")})
        result()
    elif word == "/read":
        tid = tool_id()
        assistant(
            {
                "type": "tool_use",
                "id": tid,
                "name": "Read",
                "input": {"file_path": arg},
            }
        )
        try:
            tool_output(tid, open(arg).read())
        except OSError as e:
            tool_output(tid, str(e), is_error=True)
        assistant({"type": "text", "text": "Read it."})
        result()
    elif word == "/readn":
        tid = tool_id()
        assistant(
            {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": arg}}
        )
        body = open(arg).read().splitlines()
        tool_output(tid, "\n".join(f"{n:6}\t{ln}" for n, ln in enumerate(body, 1)))
        assistant({"type": "text", "text": "Read it."})
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
    elif word == "/script":
        said = []
        for step in json.loads(arg):
            if step[0] == "sleep":
                if wait(float(step[1])):
                    interrupted.clear()
                    result(is_error=True, subtype="error_during_execution")
                    return
                continue
            if step[0] == "say":
                assistant({"type": "text", "text": step[1]})
                continue
            tool, args = step
            tid = tool_id()
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
            said.append(f"{tool} {'ok' if ok else 'error: ' + text}")
        assistant({"type": "text", "text": "script: " + "; ".join(said)})
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
    elif word == "/stream":
        n, _, tall = arg.partition(" ")
        # `/stream N tall`: one paragraph per delta, a reply taller than the view.
        sep = " \n\n" if tall == "tall" else " "
        stem = "para" if tall == "tall" else "chunk"
        stream_text(sep.join(f"{stem}{k}" for k in range(1, int(n or 3) + 1)))
        result()
    elif word == "/argv":
        assistant({"type": "text", "text": "argv: " + json.dumps(sys.argv[1:])})
        result()
    elif word == "/recall":
        assistant({"type": "text", "text": "earlier: " + " | ".join(before)})
        result()
    elif word == "/quit":
        assistant({"type": "text", "text": "bye"})
        result()
        threading.Timer(float(arg or 0.2), os._exit, (0,)).start()
    elif word == "spend":
        # A turn that costs ``arg`` dollars, by Claude's running total.
        assistant({"type": "text", "text": f"cost {arg}"})
        result(cost=float(arg))
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


def oneshot() -> None:
    mode = os.environ.get("FAKE_CLAUDE_ONESHOT", "")
    # As the real CLI: the prompt after --, else after -p, else on stdin.
    after = "--" if "--" in sys.argv else "-p"
    i = sys.argv.index(after) + 1
    prompt = sys.argv[i] if i < len(sys.argv) else ""
    if (
        after == "-p"
        and (not prompt or prompt.startswith("-"))
        and not sys.stdin.isatty()
    ):
        prompt = sys.stdin.read()
    elif after == "-p" and prompt.startswith("-"):
        # Like the real CLI: a prompt opening on "-" reads as an option unless
        # it comes after --.
        print(f"error: unknown option '{prompt}'", file=sys.stderr)
        sys.exit(1)
    with open(os.path.join(HOME, "oneshot.log"), "a") as f:
        f.write(f"{os.getpid()}\n")
    with open(os.path.join(HOME, "oneshot.pid"), "w") as f:
        f.write(str(os.getpid()))
    if mode == "fail":
        sys.exit(1)
    if mode == "garbage":
        print("not json")
        return
    if mode == "empty":
        time.sleep(float(os.environ.get("FAKE_CLAUDE_ONESHOT_SLEEP", "0")))
        print(json.dumps({"type": "result", "result": "", "total_cost_usd": 0.004}))
        return
    if mode == "slow":
        time.sleep(float(os.environ.get("FAKE_CLAUDE_ONESHOT_SLEEP", "120")))
    schema = json.loads(sys.argv[sys.argv.index("--json-schema") + 1])
    if "answer" in schema.get("properties", {}):
        asked = [
            ln.removeprefix("The person's side question: ")
            for ln in prompt.splitlines()
            if ln.startswith("The person's side question: ")
        ]
        q = asked[-1] if asked else ""
        out = {"answer": f"btw of: {q}", "needs_more": "[[more]]" in q}
    else:
        lines = [ln for ln in prompt.splitlines() if ln.startswith("user: ")]
        last = lines[-1].removeprefix("user: ") if lines else "nothing"
        out = {
            "context": f"recap of: {last}",
            "ask": "answer it" if "?" in prompt else "",
        }
    print(
        json.dumps(
            {
                "type": "result",
                "result": json.dumps(out),
                "structured_output": out,
                "total_cost_usd": 0.004,
                "duration_ms": 1800,
            }
        )
    )


def main() -> None:
    if "-p" in sys.argv and _arg("--output-format") == "json":
        if "--json-schema" not in sys.argv:
            probe()
            return
        oneshot()
        return
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
