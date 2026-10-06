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
    anything else  text that quotes the prompt, then a result.

An interrupt ``control_request`` ends a running script with an error result.

``--resume <id>`` keeps that session id, as Claude does; without it the fake
mints one. Each id's prompts are appended to ``$FAKE_CLAUDE_HOME/<id>.prompts``
(default: the system temp dir), which is what ``/recall`` reads, so a test can
prove a resumed process has its earlier context.

With ``FAKE_CLAUDE_REPLAY=<store file>``, the first prompt instead replays the
Claude lines of an aegis2 store, ``FAKE_CLAUDE_PACE`` seconds apart (default
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
import uuid

OUT = threading.Lock()
inbox: queue.Queue = queue.Queue()
interrupted = threading.Event()
state = {"cost": 0.0, "tool": 0, "inited": False, "deaf": False}
SESSION_ID = (
    sys.argv[sys.argv.index("--resume") + 1]
    if "--resume" in sys.argv
    else str(uuid.uuid4())
)
HOME = os.environ.get("FAKE_CLAUDE_HOME") or tempfile.gettempdir()


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


def result(is_error: bool = False, subtype: str = "success") -> None:
    state["cost"] += 0.01
    emit(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "duration_ms": 100,
            "total_cost_usd": round(state["cost"], 4),
            "stop_reason": None if is_error else "end_turn",
            "modelUsage": {"fake-model": {"contextWindow": 200000}},
        }
    )


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
    if not state["inited"]:
        state["inited"] = True
        emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": SESSION_ID,
                "model": "fake-model",
                "claude_code_version": "0.0-fake",
            }
        )
    echo(text)
    before = earlier_prompts()
    with open(prompts_file(), "a") as f:
        f.write(json.dumps(text) + "\n")
    word, _, arg = text.partition(" ")
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
            emit(
                {
                    "type": "control_response",
                    "response": {
                        "request_id": msg.get("request_id"),
                        "subtype": "success",
                    },
                }
            )
            interrupted.set()
        elif msg.get("type") == "user":
            interrupted.clear()
            inbox.put(msg["message"]["content"])
    # stdin closed: leave like claude does
    time.sleep(0.05)


if __name__ == "__main__":
    main()
