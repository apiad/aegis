"""Record a real claude session as a replayable fixture.

Costs real tokens (a few cents with sonnet). The fixture keeps each kept
line's delay after the previous one, so a replay reproduces the real
arrival pattern: a burst of tool calls, a pause, then text. The pure half
(``sanitize``, ``steps_from``) is what the tests cover; ``record_fixture``
is the part that talks to the real CLI.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from aegis.bench import BenchError

RECORD_PROMPT = (
    "Read notes.md in this directory. Then write a detailed markdown "
    "explanation, about 700 words, of how a terminal user interface decides "
    "what to redraw on each frame. Use headings, a bulleted list, a table "
    "and one python code block.")

_NOTES = ("Terminal UIs keep a model of the screen and diff it against the "
          "previous frame. Synchronized output (DEC mode 2026) lets the "
          "terminal present a frame atomically.\n")

# Only the line types a replay feeds aegis. The fake writes system/init
# and result itself, per turn; anything else (a recorded rate_limit_event,
# say) is noise that aegis parses as an unknown event.
_REPLAYED = ("assistant", "user", "stream_event")
FIRST_CAP_MS = 1000.0
CAP_MS = 5000.0


def sanitize(line: dict, work: str, home: str | None = None) -> dict | None:
    """The line as a fixture keeps it, or None for a line a replay does
    not feed aegis.

    Ids are dropped and the scratch directory and home directory are
    replaced, so a committed fixture carries no path from the machine that
    recorded it.
    """
    if line.get("type") not in _REPLAYED:
        return None
    line = {k: v for k, v in line.items() if k not in ("uuid", "session_id")}
    text = json.dumps(line, ensure_ascii=False).replace(work, "/work")
    if home:
        text = text.replace(home, "/home/user")
    return json.loads(text)


def steps_from(timed: list[tuple[float, dict]], work: str,
               home: str | None = None) -> list[dict]:
    """Replay steps from ``(delay_ms, line)`` pairs.

    A dropped line's delay is carried into the next kept step, so the gap
    before the first token still includes session start. Delays are capped
    (the first at 1 s, later ones at 5 s): the fixture exists to replay an
    arrival pattern, not a slow network.
    """
    steps: list[dict] = []
    carried = 0.0
    for dt_ms, obj in timed:
        carried += dt_ms
        clean = sanitize(obj, work, home)
        if clean is None:
            continue
        cap = CAP_MS if steps else FIRST_CAP_MS
        steps.append({"dt_ms": round(min(carried, cap), 1), "mark": True,
                      "line": clean})
        carried = 0.0
    return steps


def record_fixture(out: Path, *, partial: bool, prompt: str = RECORD_PROMPT,
                   model: str = "sonnet", claude: str = "claude",
                   timeout_s: float = 600) -> int:
    """Run the real ``claude`` once on ``prompt`` and write ``out``.

    Raises ``BenchError`` rather than writing a fixture when the session
    ends without a result or with an error, so a failed login can never
    become a fixture every later run replays. Returns the step count.
    """
    work = tempfile.mkdtemp(prefix="aegis-bench-record-")
    try:
        Path(work, "notes.md").write_text(_NOTES)
        argv = [claude, "-p", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose",
                "--model", model, "--permission-mode", "bypassPermissions",
                "--setting-sources", "", "--strict-mcp-config",
                "--mcp-config", json.dumps({"mcpServers": {}})]
        if partial:
            argv.append("--include-partial-messages")
        proc = subprocess.Popen(argv, cwd=work, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"type": "user", "message": {
            "role": "user", "content": prompt}}) + "\n")
        proc.stdin.flush()
        timed: list[tuple[float, dict]] = []
        result: dict | None = None
        last = time.monotonic()
        deadline = last + timeout_s
        for raw in proc.stdout:
            now = time.monotonic()
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            timed.append(((now - last) * 1000.0, obj))
            last = now
            if obj.get("type") == "result":
                result = obj
                break
            if now > deadline:
                break
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if result is None:
            raise BenchError("claude ended without a result; no fixture "
                             "written")
        if result.get("is_error"):
            raise BenchError(f"claude reported an error: "
                             f"{str(result.get('result'))[:300]}")
        steps = steps_from(timed, work, home=str(Path.home()))
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n"
                               for s in steps))
        return len(steps)
    finally:
        shutil.rmtree(work, ignore_errors=True)
