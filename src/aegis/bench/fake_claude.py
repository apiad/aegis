"""A stand-in for ``claude -p`` that replays a workload script.

aegis drives it exactly as it drives the real CLI: stream-json user
messages on stdin, stream-json events on stdout. The fake owns the
per-turn ``system/init`` and ``result`` lines and replays everything else
from the script with its recorded delays. ``stream_event`` lines are sent
only when aegis asked for them with ``--include-partial-messages``, which
is how ``claude-stream`` detects that aegis does not stream tokens.

A step with ``mark: True`` whose line takes no marker (a tool call) still
consumes a sequence number. That leaves gaps, never collisions, which is
all the latency join needs.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

from aegis.bench.frames import marker
from aegis.bench.records import MarkerSeq, Recorder
from aegis.bench.script import inject_marker, route


def _out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _prompt_text(msg: dict) -> str:
    content = msg.get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text")


def main() -> int:
    args = sys.argv[1:]
    if "--json-schema" in args:
        # The one-shot generation call (titles, recaps). An empty result is
        # a missing answer to aegis, never an error.
        _out({"type": "result", "subtype": "success", "is_error": False,
              "result": "", "duration_ms": 0, "total_cost_usd": 0.0})
        return 0
    emit_path = Path(os.environ["AEGIS_BENCH_EMIT"])
    script = json.loads(Path(os.environ["AEGIS_BENCH_SCRIPT"]).read_text())
    speed = float(script.get("speed") or 1.0)
    partial = "--include-partial-messages" in args
    pid = os.getpid()
    session = f"bench-{pid}"
    emit = Recorder(emit_path)
    seq = MarkerSeq(emit_path.parent / "marker.seq")
    emit.write({"k": "argv", "pid": pid, "partial": partial})
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("type") != "user":
            continue
        text = _prompt_text(msg)
        emit.write({"k": "prompt", "pid": pid, "t_ns": time.monotonic_ns(),
                    "word": (text.split() or [""])[0].lower()})
        _out({"type": "system", "subtype": "init", "session_id": session,
              "model": "sonnet", "permissionMode": "bypassPermissions"})
        lines = 0
        for step in route(script, text):
            line = step["line"]
            if line.get("type") in ("system", "result"):
                continue
            if line.get("type") == "stream_event" and not partial:
                continue
            delay = float(step.get("dt_ms", 0.0)) / speed
            if delay > 0:
                time.sleep(delay / 1000.0)
            line = copy.deepcopy(line)
            line["session_id"] = session
            m = marker(seq.next()) if step.get("mark", True) else None
            marked = m is not None and inject_marker(line, m)
            t = time.monotonic_ns()
            _out(line)
            lines += 1
            if marked:
                emit.write({"k": "marker", "pid": pid, "marker": m,
                            "t_emit_ns": t})
        _out({"type": "result", "subtype": "success", "is_error": False,
              "duration_ms": 1, "session_id": session, "num_turns": 1,
              "usage": {"input_tokens": 1, "output_tokens": lines}})
        emit.write({"k": "turn_end", "pid": pid, "t_ns": time.monotonic_ns(),
                    "lines": lines})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
