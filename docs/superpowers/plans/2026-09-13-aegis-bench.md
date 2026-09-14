# aegis bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** `aegis bench`, a benchmark that drives a real aegis daemon and client in a pty and reports rendering time, latency, event-loop stalls, CPU and memory, comparable across releases.

**Architecture:** A throwaway world under `/tmp` (config, fake `claude` and `lovelaice-acp` on `PATH`) runs a real `aegis serve` with a standalone probe injected through a `python -c` launcher. A pty rig plays the terminal: it answers the DEC 2026 query, splits frames at `\e[?2026l`, and timestamps markers the fake agents embed in their output. Per-repeat JSONL (frames, emits, probe, scenario events) folds into a summary with an environment fingerprint; summaries compare with noise-aware verdicts and save to `bench/history/<host>/`.

**Tech Stack:** Python 3.13, typer, rich, ptyprocess, psutil, agent-client-protocol, Textual 8.2.x, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-aegis-bench-design.md`

**Status:** executed 2026-09-13 to 2026-09-14. Departures from these steps are recorded in the spec's implementation notes.

## Global Constraints

- Never touch the operator's daemon: every bench process runs with `AEGIS_DAEMON_DIR` inside its world and is killed by process group of a PID the bench started. Never `pkill -f`.
- Worlds live under `/tmp/aegis-bench-*`, never under `/home/apiad/Workspace` (aegis config lookup walks up).
- Strip every inherited `AEGIS_*` variable from the environment before building a world's env (the bench may itself run inside an aegis session).
- Nothing runs headless. No `App.run_test`, no `pilot.pause()` timing.
- Timings are never asserted in pytest. Unit tests cover parsing, statistics and verdicts only.
- `probe.py` has no module-level aegis imports; it must load into an older aegis release.
- Timestamps that cross processes use `time.monotonic_ns()` only.
- Commits: conventional, `git commit -- <paths>` with explicit paths, never `git add -A`, never `--amend` (shared checkout).
- Code, comments, identifiers and docs in English; match the repo's comment density (docstrings that explain *why*).

## File structure

| File | Responsibility |
|---|---|
| `src/aegis/bench/__init__.py` | package docstring, `BenchError`, `ScenarioSkipped` |
| `src/aegis/bench/records.py` | `Recorder` (append JSONL), `read_jsonl`, `MarkerSeq` (flock'd counter) |
| `src/aegis/bench/frames.py` | DEC 2026 constants, `strip_ansi`, `marker`, `find_markers`, `Frame`, `FrameSplitter` |
| `src/aegis/bench/script.py` | workload scripts: `synthetic_blocks`, `synthetic_fill`, `acp_chunks`, `load_fixture`, `make_script`, `route`, `inject_marker` |
| `src/aegis/bench/fake_claude.py` | `python -m aegis.bench.fake_claude`: stream-json replay with markers |
| `src/aegis/bench/fake_acp.py` | `python -m aegis.bench.fake_acp`: ACP v1 agent streaming chunks with markers |
| `src/aegis/bench/launcher.py` | `Target`, `resolve_target`, `stage_probe`, `aegis_argv` |
| `src/aegis/bench/world.py` | `World`, `build_world`, `start_daemon`, `client_argv`, `teardown` |
| `src/aegis/bench/probe.py` | standalone in-process instrumentation, `install()` |
| `src/aegis/bench/rig.py` | `Rig` (pty terminal stand-in), `pump`, `wait_until` |
| `src/aegis/bench/metrics.py` | `MetricSpec`, `METRICS`, `pct`, `repeat_metrics`, `summarize` |
| `src/aegis/bench/compare.py` | `verdict`, `compare`, `CompareError` |
| `src/aegis/bench/report.py` | rich tables and `render_markdown` |
| `src/aegis/bench/scenarios.py` | `ScenarioContext`, scenario functions, `SCENARIOS`, `DEFAULT`, `QUICK` |
| `src/aegis/bench/runner.py` | `RunOptions`, `fingerprint`, `run`, `save_history`, `history_dir`, `runs_dir` |
| `src/aegis/bench/record.py` | `record_fixture` against the real `claude` |
| `src/aegis/bench/fixtures/*.jsonl` | recorded sessions (committed) |
| `src/aegis/cli_bench.py` | typer subapp: run, list, compare, history, record, selftest |
| `src/aegis/cli.py` | register the subapp (two lines) |
| `tests/bench/__init__.py`, `tests/bench/test_*.py` | hermetic unit tests + one opt-in end-to-end |
| `know-how/benchmarking.md` | procedure doc |
| `AGENTS.md`, `know-how/releasing.md`, `CHANGELOG.md` | index, release step, changelog |
| `bench/history/zion/*.json` | saved summaries |

---

### Task 1: records and frames

**Files:**
- Create: `src/aegis/bench/__init__.py`, `src/aegis/bench/records.py`, `src/aegis/bench/frames.py`
- Test: `tests/bench/__init__.py` (empty), `tests/bench/test_frames.py`, `tests/bench/test_records.py`

**Interfaces:**
- Produces: `BenchError(RuntimeError)`, `ScenarioSkipped(Exception)`; `Recorder(path).write(rec: dict)`, `Recorder.close()`; `read_jsonl(path) -> list[dict]` (missing file returns `[]`, damaged lines skipped); `MarkerSeq(path).next() -> int`; `SYNC_BEGIN, SYNC_END, SYNC_QUERY, SYNC_REPLY: bytes`; `strip_ansi(data: bytes) -> str`; `marker(n: int) -> str`; `find_markers(text: str) -> list[str]`; `Frame(t_ns: int, nbytes: int, text: str)`; `FrameSplitter().feed(chunk: bytes, t_ns: int) -> list[Frame]`, attributes `queries: int`, `saw_sync_begin: bool`.

- [x] **Step 1: Write the failing tests**

```python
# tests/bench/test_frames.py
from aegis.bench.frames import (
    SYNC_BEGIN, SYNC_END, SYNC_QUERY, FrameSplitter, find_markers, marker,
    strip_ansi)


def test_marker_format_round_trips():
    assert marker(7) == "«b0007»"
    assert find_markers("x «b0007» y «b12345»") == ["«b0007»", "«b12345»"]


def test_strip_ansi_keeps_text_through_sgr_cursor_and_osc():
    raw = (b"\x1b[1;5H\x1b[38;2;10;20;30mline \xc2\xabb0001\xc2\xbb\x1b[0m"
           b"\x1b]0;title\x07\x1b(B\x1b7")
    assert strip_ansi(raw) == "line «b0001»"


def test_frames_split_on_sync_end_across_chunks():
    s = FrameSplitter()
    assert s.feed(SYNC_BEGIN + b"hello \xc2\xab", 1) == []
    frames = s.feed(b"b0001\xc2\xbb" + SYNC_END[:3], 2)
    assert frames == []
    frames = s.feed(SYNC_END[3:] + SYNC_BEGIN + b"next", 3)
    assert [f.text for f in frames] == ["hello «b0001»"]
    assert frames[0].t_ns == 3
    assert s.saw_sync_begin


def test_query_is_counted_even_when_split_and_removed_from_frames():
    s = FrameSplitter()
    s.feed(b"a" + SYNC_QUERY[:4], 1)
    s.feed(SYNC_QUERY[4:] + b"b" + SYNC_END, 2)
    assert s.queries == 1
    assert s.saw_sync_begin is False


def test_frame_nbytes_counts_raw_bytes():
    s = FrameSplitter()
    (f,) = s.feed(b"\x1b[1mab" + SYNC_END, 5)
    assert f.nbytes == len(b"\x1b[1mab")
```

```python
# tests/bench/test_records.py
from aegis.bench.records import MarkerSeq, Recorder, read_jsonl


def test_recorder_appends_and_read_skips_damage(tmp_path):
    p = tmp_path / "x.jsonl"
    r = Recorder(p)
    r.write({"k": "a", "v": 1})
    r.close()
    with p.open("a") as fh:
        fh.write("{not json\n")
    Recorder(p).write({"k": "b"})
    assert [x["k"] for x in read_jsonl(p)] == ["a", "b"]


def test_read_missing_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_marker_seq_is_shared_across_instances(tmp_path):
    a, b = MarkerSeq(tmp_path / "seq"), MarkerSeq(tmp_path / "seq")
    assert [a.next(), b.next(), a.next()] == [1, 2, 3]
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/bench -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.bench'`.

- [x] **Step 3: Implement**

```python
# src/aegis/bench/__init__.py
"""aegis bench: drive a real daemon and client in a pty and measure them.

Spec: ``docs/superpowers/specs/2026-09-13-aegis-bench-design.md``.
"""
from __future__ import annotations


class BenchError(RuntimeError):
    """A benchmark could not produce a trustworthy measurement."""


class ScenarioSkipped(Exception):
    """A scenario does not apply to this target; the reason is reported."""
```

```python
# src/aegis/bench/records.py
"""JSONL records shared by the rig, the fake agents and the probe's reader.

Each process appends its own file, so no writer ever contends with another
for the same fd. ``MarkerSeq`` is the one shared resource: several fake
agents (one per tab) must never mint the same marker.
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, rec: dict) -> None:
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def read_jsonl(path: Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


class MarkerSeq:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def next(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            n = int(fh.read().strip() or 0) + 1
            fh.seek(0)
            fh.truncate()
            fh.write(str(n))
        return n
```

```python
# src/aegis/bench/frames.py
"""Terminal output as the rig sees it: DEC 2026 frames and markers.

Textual wraps each screen update in synchronized output once the terminal
confirms support, so ``\\e[?2026l`` is the end of one complete frame. The
rig answers the support query itself; a target that never negotiates
produces no frames, which the run reports as a failed gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SYNC_BEGIN = b"\x1b[?2026h"
SYNC_END = b"\x1b[?2026l"
SYNC_QUERY = b"\x1b[?2026$p"
SYNC_REPLY = b"\x1b[?2026;2$y"

_ANSI = re.compile(
    rb"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"   # CSI
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"          # OSC
    rb"|\x1bP[^\x1b]*\x1b\\"                        # DCS
    rb"|\x1b[\x20-\x2f]+[\x30-\x7e]"                # nF, e.g. charset
    rb"|\x1b[\x30-\x7e]")                           # Fp / Fe / Fs
_MARKER = re.compile(r"«b\d{4,}»")


def strip_ansi(data: bytes) -> str:
    return _ANSI.sub(b"", data).decode("utf-8", "replace")


def marker(n: int) -> str:
    return f"«b{n:04d}»"


def find_markers(text: str) -> list[str]:
    return _MARKER.findall(text)


@dataclass(frozen=True)
class Frame:
    t_ns: int
    nbytes: int
    text: str


class FrameSplitter:
    def __init__(self) -> None:
        self._buf = b""
        self.queries = 0
        self.saw_sync_begin = False

    def feed(self, chunk: bytes, t_ns: int) -> list[Frame]:
        self._buf += chunk
        n = self._buf.count(SYNC_QUERY)
        if n:
            self.queries += n
            self._buf = self._buf.replace(SYNC_QUERY, b"")
        if SYNC_BEGIN in self._buf:
            self.saw_sync_begin = True
        frames: list[Frame] = []
        while (i := self._buf.find(SYNC_END)) >= 0:
            raw = self._buf[:i].replace(SYNC_BEGIN, b"")
            self._buf = self._buf[i + len(SYNC_END):]
            frames.append(Frame(t_ns=t_ns, nbytes=i, text=strip_ansi(raw)))
        return frames
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/bench -q`
Expected: PASS (8 tests). If `test_frame_nbytes_counts_raw_bytes` fails because `SYNC_BEGIN` precedes, `nbytes` is the raw length up to `SYNC_END`, which is the bytes the terminal parsed for this frame.

- [x] **Step 5: Commit**

```bash
git add src/aegis/bench/__init__.py src/aegis/bench/records.py src/aegis/bench/frames.py tests/bench/__init__.py tests/bench/test_frames.py tests/bench/test_records.py
git commit -m "feat(bench): frame splitting, markers and JSONL records" -- src/aegis/bench tests/bench
```

---

### Task 2: workload scripts and the fake claude

**Files:**
- Create: `src/aegis/bench/script.py`, `src/aegis/bench/fake_claude.py`
- Test: `tests/bench/test_script.py`, `tests/bench/test_fake_claude.py`

**Interfaces:**
- Consumes: `marker`, `MarkerSeq`, `Recorder` (Task 1).
- Produces:
  - Step shape: claude steps `{"dt_ms": float, "line": dict, "mark": bool}`; ACP steps `{"dt_ms": float, "chunk": str, "mark": bool}`.
  - Script shape: `{"speed": float, "prompts": {word: [steps]}, "default": [steps]}`.
  - `synthetic_blocks(n: int, gap_ms: float, words: int = 12, mark: bool = True) -> list[dict]`
  - `synthetic_fill(cycles: int) -> list[dict]` (text + Read tool_use + tool_result per cycle, unmarked, zero delay; about two mounted blocks per cycle)
  - `acp_chunks(n: int, rate_hz: float, mark_every: int = 5, mark: bool = True) -> list[dict]`
  - `load_fixture(name: str) -> list[dict]` from `aegis/bench/fixtures/<name>.jsonl`
  - `make_script(prompts: dict[str, list[dict]], *, speed: float = 1.0, default: list[dict] | None = None) -> dict`
  - `route(script: dict, prompt_text: str) -> list[dict]` (first word, lowercased; falls back to `default`, then to a 1-block ack)
  - `inject_marker(line: dict, m: str) -> bool` (prefixes the first text block, or appends to a `stream_event` text delta)
  - Env contract for fakes: `AEGIS_BENCH_SCRIPT` (script JSON path), `AEGIS_BENCH_EMIT` (emit JSONL path; the marker counter lives beside it as `marker.seq`).
  - Emit records: `{"k":"argv","pid","partial":bool}`, `{"k":"prompt","pid","t_ns","word"}`, `{"k":"marker","pid","marker","t_emit_ns"}`, `{"k":"turn_end","pid","t_ns","lines":int}`.

- [x] **Step 1: Write the failing tests**

```python
# tests/bench/test_script.py
from aegis.bench.script import (
    acp_chunks, inject_marker, make_script, route, synthetic_blocks,
    synthetic_fill)


def test_route_uses_first_word_then_default_then_ack():
    s = make_script({"go": synthetic_blocks(2, 0)},
                    default=synthetic_blocks(1, 0))
    assert len(route(s, "Go now")) == 2
    assert len(route(s, "other")) == 1
    assert len(route(make_script({}), "x")) == 1


def test_inject_marker_into_text_and_stream_delta():
    line = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hello"}]}}
    assert inject_marker(line, "«b0001»")
    assert line["message"]["content"][0]["text"] == "«b0001» hello"
    ev = {"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "abc"}}}
    assert inject_marker(ev, "«b0002»")
    assert ev["event"]["delta"]["text"] == "abc «b0002» "
    tool = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}}
    assert not inject_marker(tool, "«b0003»")


def test_fill_is_unmarked_and_alternates_kinds():
    steps = synthetic_fill(3)
    assert all(s["mark"] is False for s in steps)
    kinds = [s["line"]["type"] for s in steps]
    assert kinds == ["assistant", "assistant", "user"] * 3


def test_acp_chunks_marks_every_nth():
    steps = acp_chunks(10, 50, mark_every=5)
    assert [s["mark"] for s in steps].count(True) == 2
    assert steps[1]["dt_ms"] == 20.0
```

```python
# tests/bench/test_fake_claude.py
import json
import subprocess
import sys

from aegis.bench.records import read_jsonl
from aegis.bench.script import make_script, synthetic_blocks
from aegis.events import AssistantText, Result, SystemInit, parse


def _run(tmp_path, *extra, stdin):
    script = tmp_path / "script.json"
    script.write_text(json.dumps(make_script({"go": synthetic_blocks(3, 0)})))
    env = {"AEGIS_BENCH_SCRIPT": str(script),
           "AEGIS_BENCH_EMIT": str(tmp_path / "emit.jsonl"),
           "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, "-m", "aegis.bench.fake_claude", "-p", *extra],
        input=stdin, capture_output=True, text=True, env=env, timeout=30)


def test_turn_parses_as_aegis_events_with_markers(tmp_path):
    msg = json.dumps({"type": "user",
                      "message": {"role": "user", "content": "go"}})
    out = _run(tmp_path, stdin=msg + "\n")
    assert out.returncode == 0, out.stderr
    events = [parse(line) for line in out.stdout.splitlines()]
    assert isinstance(events[0], SystemInit)
    texts = [e for e in events if isinstance(e, AssistantText)]
    assert len(texts) == 3 and all("«b" in e.text for e in texts)
    assert isinstance(events[-1], Result)
    emit = read_jsonl(tmp_path / "emit.jsonl")
    kinds = [r["k"] for r in emit]
    assert kinds[:2] == ["argv", "prompt"]
    assert kinds.count("marker") == 3 and kinds[-1] == "turn_end"


def test_json_schema_one_shot_returns_an_empty_envelope(tmp_path):
    out = _run(tmp_path, "--json-schema", "{}", stdin="")
    assert json.loads(out.stdout)["result"] == ""


def test_stream_events_are_dropped_without_the_partial_flag(tmp_path):
    script = tmp_path / "script.json"
    ev = {"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "x"}}}
    script.write_text(json.dumps(make_script(
        {"go": [{"dt_ms": 0, "line": ev, "mark": True}]})))
    env = {"AEGIS_BENCH_SCRIPT": str(script),
           "AEGIS_BENCH_EMIT": str(tmp_path / "emit.jsonl")}
    msg = json.dumps({"type": "user", "message": {"content": "go"}})
    out = subprocess.run([sys.executable, "-m", "aegis.bench.fake_claude"],
                         input=msg + "\n", capture_output=True, text=True,
                         env=env, timeout=30)
    assert "stream_event" not in out.stdout
    assert read_jsonl(tmp_path / "emit.jsonl")[0] == {
        "k": "argv", "pid": read_jsonl(tmp_path / "emit.jsonl")[0]["pid"],
        "partial": False}
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/bench/test_script.py tests/bench/test_fake_claude.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.bench.script'`.

- [x] **Step 3: Implement `script.py`**

```python
# src/aegis/bench/script.py
"""Workload scripts the fake agents replay.

A script maps the first word of a prompt to a list of steps, so one fake
binary serves every tab of a world: ``fill`` mounts history without
markers, ``bg`` streams in a background tab without markers, ``go`` is the
measured stream. Markers are only minted for ``mark: True`` steps, so a
hidden tab never produces a marker the rig could not possibly see.
"""
from __future__ import annotations

import json
from importlib import resources

_LOREM = ("the compositor diffs spans against the previous frame and writes "
          "only what changed while the layout pass measures every widget").split()


def _words(n: int, offset: int) -> str:
    return " ".join(_LOREM[(offset + i) % len(_LOREM)] for i in range(n))


def _assistant_text(text: str, mid: str) -> dict:
    return {"type": "assistant", "message": {
        "id": mid, "role": "assistant",
        "content": [{"type": "text", "text": text}]}}


def synthetic_blocks(n: int, gap_ms: float, words: int = 12,
                     mark: bool = True) -> list[dict]:
    return [{"dt_ms": float(gap_ms), "mark": mark,
             "line": _assistant_text(f"{_words(words, i)}\n\n", f"sb{i}")}
            for i in range(n)]


def synthetic_fill(cycles: int) -> list[dict]:
    steps: list[dict] = []
    for i in range(cycles):
        tid = f"fill-{i}"
        steps.append({"dt_ms": 0.0, "mark": False, "line": _assistant_text(
            f"{_words(30, i)}\n\n", f"f{i}")})
        steps.append({"dt_ms": 0.0, "mark": False, "line": {
            "type": "assistant", "message": {
                "id": f"ft{i}", "role": "assistant",
                "content": [{"type": "tool_use", "id": tid, "name": "Read",
                             "input": {"file_path": f"/work/file{i}.py"}}]}}})
        steps.append({"dt_ms": 0.0, "mark": False, "line": {
            "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid,
                 "content": _words(40, i)}]}}})
    return steps


def acp_chunks(n: int, rate_hz: float, mark_every: int = 5,
               mark: bool = True) -> list[dict]:
    gap = 1000.0 / rate_hz
    steps = []
    for i in range(n):
        text = _words(3, i) + (" \n\n" if i % 20 == 19 else " ")
        steps.append({"dt_ms": gap, "chunk": text,
                      "mark": mark and i % mark_every == mark_every - 1})
    return steps


def load_fixture(name: str) -> list[dict]:
    raw = resources.files("aegis.bench").joinpath(
        "fixtures", f"{name}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def make_script(prompts: dict[str, list[dict]], *, speed: float = 1.0,
                default: list[dict] | None = None) -> dict:
    return {"speed": speed, "prompts": prompts, "default": default}


def route(script: dict, prompt_text: str) -> list[dict]:
    word = (prompt_text.strip().split() or [""])[0].lower()
    steps = script.get("prompts", {}).get(word)
    if steps is None:
        steps = script.get("default")
    if steps is None:
        steps = synthetic_blocks(1, 0, mark=False)
    return steps


def inject_marker(line: dict, m: str) -> bool:
    if line.get("type") == "assistant":
        for block in line.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = f"{m} {block.get('text', '')}"
                return True
        return False
    if line.get("type") == "stream_event":
        delta = line.get("event", {}).get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            delta["text"] = f"{delta.get('text', '')} {m} "
            return True
    return False
```

- [x] **Step 4: Implement `fake_claude.py`**

```python
# src/aegis/bench/fake_claude.py
"""A stand-in for ``claude -p`` that replays a workload script.

aegis drives it exactly as it drives the real CLI: stream-json user
messages on stdin, stream-json events on stdout. The fake owns the
per-turn ``system/init`` and ``result`` lines and replays everything else
from the script with its recorded delays. ``stream_event`` lines are sent
only when aegis asked for them with ``--include-partial-messages``, which
is how ``claude-stream`` detects that aegis does not stream tokens.
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
```

A step with `mark: True` whose line takes no marker (a tool call) still consumes a sequence number. That leaves gaps, never collisions, which is all the join needs.

- [x] **Step 5: Run tests**

Run: `uv run pytest tests/bench -q`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add src/aegis/bench/script.py src/aegis/bench/fake_claude.py tests/bench/test_script.py tests/bench/test_fake_claude.py
git commit -m "feat(bench): workload scripts and a stream-json fake claude" -- src/aegis/bench tests/bench
```

---

### Task 3: launcher, world and rig (the vertical slice)

**Files:**
- Create: `src/aegis/bench/launcher.py`, `src/aegis/bench/world.py`, `src/aegis/bench/rig.py`
- Test: `tests/bench/test_launcher.py`, `tests/bench/test_e2e.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces:
  - `Target(label: str, python: tuple[str, ...], version: str, build: str, aegis_file: str, textual: str, rich: str, python_version: str, topology: str)`; `topology in {"daemon", "in-process"}`.
  - `resolve_target(spec: str | None) -> Target` (None = this interpreter; `X.Y.Z` = `uvx --from aegis-harness==X.Y.Z python`; otherwise a path to a python).
  - `stage_probe(run_dir: Path) -> Path` (copies `probe.py` to `run_dir/_probe/aegis_bench_probe.py`, returns the directory).
  - `aegis_argv(target: Target, args: list[str], *, probe_dir: Path | None) -> list[str]`.
  - `World(root, run_dir, env, target, probe_dir, daemon: subprocess.Popen | None)`, `World.socket -> Path`.
  - `build_world(run_dir: Path, target: Target, *, script: dict, default_agent: str = "bench", sabotage_ms: int = 0) -> World`.
  - `start_daemon(world: World, *, wrap: list[str] | None = None, timeout_s: float = 60) -> float` (boot ms; raises `BenchError` with the `serve.log` tail).
  - `client_argv(world: World, *, view: str, wrap: list[str] | None = None) -> list[str]`.
  - `teardown(world: World, *, keep: bool = False) -> None`.
  - `Rig(argv, *, cwd, env, cols, rows, label, recorder: Recorder)`: `start()`, `pid`, `closed`, `frames: int`, `first_frame_ns`, `markers_seen: dict[str, int]`, `listeners: list[Callable[[Frame], None]]`, `on_readable()`, `write(data: bytes) -> int`, `resize(cols, rows) -> int`, `contains(s: str) -> bool` (anywhere in the text seen so far, last 200 KB), `last_frame_ns`, `sample_proc()`, `close(timeout_s=5)`.
  - `pump(rigs: list[Rig], timeout_s: float = 0.02) -> None`; `wait_until(rigs, predicate: Callable[[], bool], timeout_s: float) -> bool`.

- [x] **Step 1: Write the failing unit test**

```python
# tests/bench/test_launcher.py
import sys

from aegis.bench.launcher import aegis_argv, resolve_target


def test_current_target_is_this_interpreter_in_daemon_topology():
    t = resolve_target(None)
    assert t.python == (sys.executable,)
    assert t.topology == "daemon"
    assert t.aegis_file.endswith("aegis/__init__.py")


def test_argv_installs_probe_before_importing_aegis(tmp_path):
    t = resolve_target(None)
    argv = aegis_argv(t, ["serve", "--cwd", "/x"], probe_dir=tmp_path)
    code = argv[argv.index("-c") + 1]
    assert code.index("aegis_bench_probe") < code.index("from aegis.cli")
    assert "'serve', '--cwd', '/x'" in code
```

- [x] **Step 2: Implement `launcher.py`**

```python
# src/aegis/bench/launcher.py
"""Which aegis to run, and how to start it with the probe inside.

The probe goes in through ``python -c`` so production code carries no
benchmark hook, and so an old release from PyPI can be measured with
today's probe. Topology is read from the target itself: a build whose CLI
has ``attach`` runs as daemon + client, anything older runs the TUI
in-process.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError

_INTROSPECT = (
    "import json,sys,importlib.metadata as m,aegis,aegis.cli as c\n"
    "try:\n from aegis.version import BUILD as b\n"
    "except Exception:\n b=m.version('aegis-harness')\n"
    "print(json.dumps({'version':m.version('aegis-harness'),'build':b,"
    "'aegis_file':aegis.__file__,'textual':m.version('textual'),"
    "'rich':m.version('rich'),'python':sys.version.split()[0],"
    "'attach':hasattr(c,'attach')}))")


@dataclass(frozen=True)
class Target:
    label: str
    python: tuple[str, ...]
    version: str
    build: str
    aegis_file: str
    textual: str
    rich: str
    python_version: str
    topology: str


def resolve_target(spec: str | None) -> Target:
    if spec is None:
        python, label = (sys.executable,), "current"
    elif re.fullmatch(r"\d+\.\d+\.\d+", spec):
        python = ("uvx", "--from", f"aegis-harness=={spec}", "python")
        label = spec
    else:
        path = Path(spec).expanduser()
        if not path.exists():
            raise BenchError(f"target {spec!r} is neither X.Y.Z nor a python")
        python, label = (str(path),), str(path)
    proc = subprocess.run([*python, "-c", _INTROSPECT], capture_output=True,
                          text=True, timeout=600)
    if proc.returncode != 0:
        raise BenchError(f"target {label}: cannot import aegis:\n"
                         f"{proc.stderr[-2000:]}")
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    return Target(label=label, python=python, version=info["version"],
                  build=info["build"], aegis_file=info["aegis_file"],
                  textual=info["textual"], rich=info["rich"],
                  python_version=info["python"],
                  topology="daemon" if info["attach"] else "in-process")


def stage_probe(run_dir: Path) -> Path:
    dest = Path(run_dir) / "_probe"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("probe.py"),
                    dest / "aegis_bench_probe.py")
    return dest


def aegis_argv(target: Target, args: list[str], *,
               probe_dir: Path | None) -> list[str]:
    head = ""
    if probe_dir is not None:
        head = (f"sys.path.insert(0,{str(probe_dir)!r});"
                "import aegis_bench_probe;aegis_bench_probe.install();")
    code = (f"import sys;{head}sys.argv=['aegis',*{list(args)!r}];"
            "from aegis.cli import main;main()")
    return [*target.python, "-c", code]
```

- [x] **Step 3: Implement `world.py`**

```python
# src/aegis/bench/world.py
"""A throwaway aegis world: config, fake agents on PATH, its own daemon.

The root is under /tmp because aegis resolves its project by walking up to
the nearest ``.aegis.yaml``; a root inside the workspace would pick up the
operator's config. ``AEGIS_DAEMON_DIR`` keeps the daemon out of the
operator's registry, and the daemon runs in its own session so teardown
can kill its whole process group, fake agents included, by a PID the
bench started.
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import json
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError
from aegis.bench.launcher import Target, aegis_argv, stage_probe

_CONFIG = """agents:
  bench:
    provider: claude-code
    model: sonnet
    effort: low
    permission: full
  bench-acp:
    provider: lovelaice
    model: bench
default_agent: {default}
"""


@dataclass
class World:
    root: Path
    run_dir: Path
    env: dict[str, str]
    target: Target
    probe_dir: Path
    daemon: subprocess.Popen | None = None

    @property
    def socket(self) -> Path:
        return self.root / ".aegis" / "state" / "daemon.sock"


def build_world(run_dir: Path, target: Target, *, script: dict,
                default_agent: str = "bench", sabotage_ms: int = 0) -> World:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="aegis-bench-"))
    (root / ".aegis.yaml").write_text(_CONFIG.format(default=default_agent))
    bin_dir = root / ".bench-bin"
    bin_dir.mkdir()
    for name, module in (("claude", "aegis.bench.fake_claude"),
                         ("lovelaice-acp", "aegis.bench.fake_acp")):
        shim = bin_dir / name
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                        f"-m {module} \"$@\"\n")
        shim.chmod(0o755)
    script_path = run_dir / "script.json"
    script_path.write_text(json.dumps(script))
    env = {k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")}
    env.update({
        "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        "AEGIS_DAEMON_DIR": str(root / ".daemons"),
        "AEGIS_IDLE_TIMEOUT": "0",
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "AEGIS_BENCH_SCRIPT": str(script_path),
        "AEGIS_BENCH_EMIT": str(run_dir / "emit.jsonl"),
        "AEGIS_BENCH_PROBE": str(run_dir / "probe.jsonl"),
    })
    if sabotage_ms:
        env["AEGIS_BENCH_SABOTAGE_MS"] = str(sabotage_ms)
    return World(root=root, run_dir=run_dir, env=env, target=target,
                 probe_dir=stage_probe(run_dir))


def _accepts(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_daemon(world: World, *, wrap: list[str] | None = None,
                 timeout_s: float = 60) -> float:
    argv = aegis_argv(world.target, ["serve", "--cwd", str(world.root)],
                      probe_dir=world.probe_dir)
    log = (world.run_dir / "serve.log").open("wb")
    t0 = time.monotonic_ns()
    world.daemon = subprocess.Popen(
        [*(wrap or []), *argv], cwd=world.root, env=world.env,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)
    deadline = time.monotonic() + timeout_s
    while not (world.socket.exists() and _accepts(world.socket)):
        if world.daemon.poll() is not None or time.monotonic() > deadline:
            tail = (world.run_dir / "serve.log").read_text(errors="replace")
            raise BenchError(f"daemon did not come up (rc="
                             f"{world.daemon.poll()}):\n{tail[-2000:]}")
        time.sleep(0.01)
    return (time.monotonic_ns() - t0) / 1e6


def client_argv(world: World, *, view: str,
                wrap: list[str] | None = None) -> list[str]:
    if world.target.topology == "daemon":
        args = ["attach", "--cwd", str(world.root), "--view", view]
        return aegis_argv(world.target, args, probe_dir=None)
    argv = aegis_argv(world.target, ["--cwd", str(world.root)],
                      probe_dir=world.probe_dir)
    return [*(wrap or []), *argv]


def _kill_group(pid: int) -> None:
    for sig, wait_s in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)


def teardown(world: World, *, keep: bool = False) -> None:
    if world.daemon is not None:
        _kill_group(world.daemon.pid)
        world.daemon.wait(timeout=5)
    if not keep:
        shutil.rmtree(world.root, ignore_errors=True)
```

- [x] **Step 4: Implement `rig.py`**

```python
# src/aegis/bench/rig.py
"""The terminal the benchmark pretends to be.

It owns the pty master, answers Textual's DEC 2026 support query the way a
modern terminal does, and records one line per complete frame with the
monotonic time its last byte arrived. It measures up to the bytes reaching
the terminal, not the emulator drawing them: that cost is the same for
every TUI and a pty cannot see it.
"""
from __future__ import annotations

import os
import select
import time
from collections.abc import Callable
from pathlib import Path

import psutil
from ptyprocess import PtyProcess

from aegis.bench.frames import SYNC_REPLY, Frame, FrameSplitter, find_markers
from aegis.bench.records import Recorder

_TEXT_KEEP = 200_000


class Rig:
    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str],
                 cols: int, rows: int, label: str,
                 recorder: Recorder) -> None:
        self.argv, self.cwd, self.env = argv, cwd, env
        self.cols, self.rows, self.label = cols, rows, label
        self.recorder = recorder
        self.splitter = FrameSplitter()
        self.proc: PtyProcess | None = None
        self.closed = False
        self.frames = 0
        self.first_frame_ns: int | None = None
        self.last_frame_ns: int | None = None
        self.markers_seen: dict[str, int] = {}
        self.listeners: list[Callable[[Frame], None]] = []
        self._answered = 0
        self._text = ""
        self._last_sample = 0.0

    def start(self) -> None:
        self.t_start_ns = time.monotonic_ns()
        self.proc = PtyProcess.spawn(self.argv, cwd=str(self.cwd),
                                     env=self.env,
                                     dimensions=(self.rows, self.cols))

    @property
    def pid(self) -> int:
        assert self.proc is not None
        return self.proc.pid

    @property
    def fd(self) -> int:
        assert self.proc is not None
        return self.proc.fd

    def on_readable(self) -> None:
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            self.closed = True
            return
        t = time.monotonic_ns()
        frames = self.splitter.feed(chunk, t)
        while self._answered < self.splitter.queries:
            os.write(self.fd, SYNC_REPLY)
            self._answered += 1
        for f in frames:
            self.frames += 1
            if self.first_frame_ns is None:
                self.first_frame_ns = f.t_ns
            self.last_frame_ns = f.t_ns
            marks = find_markers(f.text)
            for m in marks:
                self.markers_seen.setdefault(m, f.t_ns)
            self._text = (self._text + f.text)[-_TEXT_KEEP:]
            self.recorder.write({"k": "frame", "client": self.label,
                                 "t_ns": f.t_ns, "nbytes": f.nbytes,
                                 "markers": marks})
            for fn in self.listeners:
                fn(f)

    def write(self, data: bytes) -> int:
        t = time.monotonic_ns()
        os.write(self.fd, data)
        return t

    def resize(self, cols: int, rows: int) -> int:
        assert self.proc is not None
        t = time.monotonic_ns()
        self.proc.setwinsize(rows, cols)
        self.cols, self.rows = cols, rows
        return t

    def contains(self, s: str) -> bool:
        return s in self._text

    @property
    def saw_sync(self) -> bool:
        return self.splitter.saw_sync_begin

    def sample_proc(self) -> None:
        now = time.monotonic()
        if self.closed or now - self._last_sample < 1.0:
            return
        self._last_sample = now
        try:
            p = psutil.Process(self.pid)
            cpu = p.cpu_times()
            rss = p.memory_info().rss
        except psutil.Error:
            return
        self.recorder.write({"k": "proc", "client": self.label,
                             "t_ns": time.monotonic_ns(),
                             "cpu_s": cpu.user + cpu.system, "rss": rss})

    def close(self, timeout_s: float = 5.0) -> None:
        if self.proc is None:
            return
        if not self.closed:
            try:
                self.write(b"\x04")  # Ctrl+D detaches
            except OSError:
                pass
            wait_until([self], lambda: self.closed, timeout_s)
        try:
            os.killpg(self.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.close(force=True)


def pump(rigs: list[Rig], timeout_s: float = 0.02) -> None:
    live = [r for r in rigs if not r.closed]
    if not live:
        time.sleep(timeout_s)
        return
    ready, _, _ = select.select([r.fd for r in live], [], [], timeout_s)
    for r in live:
        if r.fd in ready:
            r.on_readable()
        r.sample_proc()


def wait_until(rigs: list[Rig], predicate: Callable[[], bool],
               timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        pump(rigs)
    return predicate()
```

`ptyprocess` spawns the child with `setsid`, so `os.killpg(pid, …)` in `close` reaches an in-process target's fake agents too.

- [x] **Step 5: Write the end-to-end slice test (opt-in)**

```python
# tests/bench/test_e2e.py
"""End-to-end: a real daemon, a real client in a pty, the fake claude.

Opt-in (``AEGIS_BENCH_E2E=1``): it takes seconds and spawns processes.
It asserts the measurement works, never how fast anything is.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AEGIS_BENCH_E2E") != "1",
                                reason="set AEGIS_BENCH_E2E=1")


def test_markers_reach_the_terminal(tmp_path):
    from aegis.bench.launcher import resolve_target
    from aegis.bench.records import Recorder, read_jsonl
    from aegis.bench.rig import Rig, wait_until
    from aegis.bench.script import make_script, synthetic_blocks
    from aegis.bench.world import (
        build_world, client_argv, start_daemon, teardown)

    target = resolve_target(None)
    world = build_world(tmp_path, target, script=make_script(
        {"go": synthetic_blocks(20, 20)}))
    rec = Recorder(tmp_path / "frames.jsonl")
    rig = None
    try:
        start_daemon(world)
        rig = Rig(client_argv(world, view="bench-a"), cwd=world.root,
                  env=world.env, cols=120, rows=40, label="a", recorder=rec)
        rig.start()
        assert wait_until([rig], lambda: rig.contains("type a message"), 60)
        prompts = lambda: sum(1 for r in read_jsonl(tmp_path / "emit.jsonl")
                              if r["k"] == "prompt")
        rig.write(b"go")
        for _ in range(5):
            rig.write(b"\r")
            if wait_until([rig], lambda: prompts() > 0, 3):
                break
        assert prompts() == 1
        emitted = lambda: [r["marker"] for r in read_jsonl(
            tmp_path / "emit.jsonl") if r["k"] == "marker"]
        assert wait_until([rig], lambda: len(emitted()) == 20 and all(
            m in rig.markers_seen for m in emitted()), 60)
        assert rig.saw_sync
    finally:
        if rig is not None:
            rig.close()
        teardown(world)
```

- [x] **Step 6: Run the slice**

Run: `uv run pytest tests/bench/test_launcher.py -q && AEGIS_BENCH_E2E=1 uv run pytest tests/bench/test_e2e.py -q`
Expected: PASS. The spike on 2026-09-13 lost the prompt when it typed 3 s after the first frame on a cold daemon; the test types, then presses Enter until the fake records a prompt. If it still fails, read `tmp_path/serve.log` and dump `rig._text[-3000:]` before changing the retry logic, and write down in this plan what the cold-boot cause was. The world probe dir must exist even though `probe.py` does not yet: create an empty `src/aegis/bench/probe.py` with a docstring and a no-op `install()` in this task so `stage_probe` can copy it; Task 4 replaces it.

- [x] **Step 7: Confirm the teardown leaves nothing behind**

Run: `ps -eo pid,args | grep -c "[a]egis-bench-"`
Expected: `0`.

- [x] **Step 8: Commit**

```bash
git commit -m "feat(bench): pty rig, throwaway world and launcher" -- src/aegis/bench/launcher.py src/aegis/bench/world.py src/aegis/bench/rig.py src/aegis/bench/probe.py tests/bench/test_launcher.py tests/bench/test_e2e.py
```
(`git add` the new files first, by name.)

---

### Task 4: the probe

**Files:**
- Modify: `src/aegis/bench/probe.py` (replace the placeholder)
- Test: `tests/bench/test_probe.py`

**Interfaces:**
- Consumes: env `AEGIS_BENCH_PROBE` (output path), `AEGIS_BENCH_SABOTAGE_MS` (optional).
- Produces probe records:
  - `{"k":"hooks","pid","installed":[names],"optional_missing":[names]}` once
  - `{"k":"tick","t0","dur_ns","layout","compose","display","n_height","n_render_lines"}` per `Screen._on_timer_update`
  - `{"k":"layout"|"compose"|"display"|"paint","t0","dur_ns"}` for spans outside a tick
  - `{"k":"lag","t_ns","lag_ns"}` every 5 ms
  - `{"k":"sample","t_ns","cpu_s","rss","uss","threads","fds","n_height","n_render_lines","gc_ns_total","gc_ns_max"}` every second
  - `{"k":"gate","sync":bool,"headless":bool,"aegis_file":str}` on first display and whenever it changes
- `install() -> None` raises `ProbeError` when a required hook is missing, when `AEGIS_BENCH_PROBE` is unset, or when sabotage is requested and `ConversationPane._paint_streaming` is missing.

- [x] **Step 1: Write the failing tests** (in subprocesses, so patched Textual classes never leak into the test process)

```python
# tests/bench/test_probe.py
import json
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[2] / "src/aegis/bench/probe.py"


def _py(tmp_path, code, **env):
    full = {"AEGIS_BENCH_PROBE": str(tmp_path / "probe.jsonl"), **env}
    return subprocess.run(
        [sys.executable, "-c",
         f"import sys;sys.path.insert(0,{str(PROBE.parent)!r});{code}"],
        capture_output=True, text=True, env={**__import__('os').environ,
                                             **full}, timeout=60)


def test_install_reports_hooks(tmp_path):
    out = _py(tmp_path, "import probe;probe.install();probe._SINK.flush()")
    assert out.returncode == 0, out.stderr
    recs = [json.loads(l) for l in (tmp_path / "probe.jsonl").read_text().splitlines()]
    hooks = next(r for r in recs if r["k"] == "hooks")
    assert "Screen._on_timer_update" in hooks["installed"]
    assert "Widget.render_lines" in hooks["installed"]


def test_missing_required_hook_fails_loudly(tmp_path):
    code = ("import textual.screen as s;del s.Screen._refresh_layout;"
            "import probe;probe.install()")
    out = _py(tmp_path, code)
    assert out.returncode != 0
    assert "Screen._refresh_layout" in out.stderr


def test_unset_output_path_fails(tmp_path):
    out = subprocess.run(
        [sys.executable, "-c",
         f"import sys;sys.path.insert(0,{str(PROBE.parent)!r});"
         "import probe;probe.install()"],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in __import__('os').environ.items()
             if k != "AEGIS_BENCH_PROBE"})
    assert out.returncode != 0 and "AEGIS_BENCH_PROBE" in out.stderr


def test_spans_nest_inside_a_tick(tmp_path):
    code = (
        "import probe;probe.install();"
        "from textual.screen import Screen;from textual.app import App;"
        "import time\n"
        "class S: pass\n"
        "probe._CUR=None\n"
        "def inner(self): time.sleep(0.002)\n"
        "w=probe._span('layout',inner)\n"
        "t=probe._span('tick',lambda self: w(self))\n"
        "t(S());probe._SINK.flush()")
    out = _py(tmp_path, code)
    assert out.returncode == 0, out.stderr
    recs = [json.loads(l) for l in (tmp_path / "probe.jsonl").read_text().splitlines()]
    tick = next(r for r in recs if r["k"] == "tick")
    assert tick["layout"] >= 2_000_000 and tick["dur_ns"] >= tick["layout"]
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/bench/test_probe.py -q`
Expected: FAIL (placeholder has no hooks record).

- [x] **Step 3: Implement**

```python
# src/aegis/bench/probe.py
"""In-process instrumentation for aegis bench.

Standalone on purpose: the launcher copies this file next to the run and
imports it before aegis, so it must load into an aegis release older than
the bench itself. It never imports aegis at module level.

What it wraps is Textual's frame path: ``Screen._on_timer_update`` is the
frame tick; inside it ``Screen._refresh_layout`` is the layout pass,
``Compositor.render_update`` builds the update and ``App._display`` encodes
and writes it. A missing required hook raises, because a probe that
silently wraps nothing produces a green run that measured nothing.

``get_content_height`` and ``render_lines`` are counted on ``Widget``
itself; subclasses that override them are not counted.
"""
from __future__ import annotations

import asyncio
import gc
import importlib
import json
import os
import sys
import threading
import time

REQUIRED = (
    ("textual.screen", "Screen", "_on_timer_update", "tick"),
    ("textual.screen", "Screen", "_refresh_layout", "layout"),
    ("textual._compositor", "Compositor", "render_update", "compose"),
    ("textual.app", "App", "_display", "display"),
)
COUNTED = (
    ("textual.widget", "Widget", "get_content_height", "n_height"),
    ("textual.widget", "Widget", "render_lines", "n_render_lines"),
)
PAINT = ("aegis.tui.pane", "ConversationPane", "_paint_streaming", "paint")


class ProbeError(RuntimeError):
    pass


class _Sink:
    def __init__(self, path: str) -> None:
        self._fh = open(path, "a", encoding="utf-8")
        self._buf: list[dict] = []
        self._lock = threading.Lock()

    def write(self, rec: dict) -> None:
        self._buf.append(rec)

    def flush(self) -> None:
        with self._lock:
            buf, self._buf = self._buf, []
            if buf:
                self._fh.write("".join(json.dumps(r) + "\n" for r in buf))
                self._fh.flush()


_SINK: _Sink | None = None
_CUR: dict | None = None
_COUNTS = {"n_height": 0, "n_render_lines": 0}
_GC = {"start": 0, "total": 0, "max": 0}
_GATE: dict | None = None
_LAG_STARTED = False


def _span(name: str, fn):
    def wrapper(*args, **kwargs):
        global _CUR
        t0 = time.monotonic_ns()
        if name == "tick":
            outer, _CUR = _CUR, {"layout": 0, "compose": 0, "display": 0,
                                 "n_height": 0, "n_render_lines": 0}
            try:
                return fn(*args, **kwargs)
            finally:
                cur, _CUR = _CUR, outer
                _SINK.write({"k": "tick", "t0": t0,
                             "dur_ns": time.monotonic_ns() - t0, **cur})
        if name == "display" and args:
            _observe_app(args[0])
        try:
            return fn(*args, **kwargs)
        finally:
            dur = time.monotonic_ns() - t0
            if _CUR is not None and name in _CUR:
                _CUR[name] += dur
            else:
                _SINK.write({"k": name, "t0": t0, "dur_ns": dur})
    wrapper.__wrapped__ = fn
    return wrapper


def _counter(name: str, fn):
    def wrapper(*args, **kwargs):
        _COUNTS[name] += 1
        if _CUR is not None:
            _CUR[name] += 1
        return fn(*args, **kwargs)
    wrapper.__wrapped__ = fn
    return wrapper


def _sabotaged(ms: float, fn):
    def wrapper(*args, **kwargs):
        time.sleep(ms / 1000.0)
        return fn(*args, **kwargs)
    return wrapper


def _observe_app(app) -> None:
    global _GATE, _LAG_STARTED
    aegis = sys.modules.get("aegis")
    gate = {"k": "gate", "sync": bool(getattr(app, "_sync_available", False)),
            "headless": bool(getattr(app, "is_headless", False)),
            "aegis_file": getattr(aegis, "__file__", "") or ""}
    if gate != _GATE:
        _GATE = gate
        _SINK.write(dict(gate, t_ns=time.monotonic_ns()))
    if not _LAG_STARTED:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        _LAG_STARTED = True
        loop.create_task(_lag())


async def _lag() -> None:
    interval = 5_000_000
    while True:
        t = time.monotonic_ns()
        await asyncio.sleep(interval / 1e9)
        _SINK.write({"k": "lag", "t_ns": t,
                     "lag_ns": max(0, time.monotonic_ns() - t - interval)})


def _gc_cb(phase: str, info: dict) -> None:
    if phase == "start":
        _GC["start"] = time.monotonic_ns()
    elif _GC["start"]:
        d = time.monotonic_ns() - _GC["start"]
        _GC["total"] += d
        _GC["max"] = max(_GC["max"], d)


def _sampler() -> None:
    try:
        import psutil
        proc = psutil.Process()
    except Exception:  # noqa: BLE001 — memory fields stay null
        proc = None
    while True:
        rec = {"k": "sample", "t_ns": time.monotonic_ns(),
               "cpu_s": time.process_time(), "rss": None, "uss": None,
               "threads": threading.active_count(), "fds": None,
               "n_height": _COUNTS["n_height"],
               "n_render_lines": _COUNTS["n_render_lines"],
               "gc_ns_total": _GC["total"], "gc_ns_max": _GC["max"]}
        if proc is not None:
            try:
                mem = proc.memory_full_info()
                rec.update(rss=mem.rss, uss=mem.uss,
                           threads=proc.num_threads(), fds=proc.num_fds())
            except Exception:  # noqa: BLE001
                pass
        _SINK.write(rec)
        _SINK.flush()
        time.sleep(1.0)


def _patch(mod: str, cls: str, meth: str, make) -> str:
    label = f"{cls}.{meth}"
    try:
        klass = getattr(importlib.import_module(mod), cls)
    except (ImportError, AttributeError) as exc:
        raise ProbeError(f"required hook {mod}.{label} not found") from exc
    orig = klass.__dict__.get(meth)
    if orig is None:
        raise ProbeError(f"required hook {mod}.{label} not found")
    setattr(klass, meth, make(orig))
    return label


def install() -> None:
    global _SINK
    path = os.environ.get("AEGIS_BENCH_PROBE")
    if not path:
        raise ProbeError("AEGIS_BENCH_PROBE is not set")
    _SINK = _Sink(path)
    installed = []
    for mod, cls, meth, name in REQUIRED:
        installed.append(_patch(mod, cls, meth,
                                lambda f, n=name: _span(n, f)))
    for mod, cls, meth, name in COUNTED:
        installed.append(_patch(mod, cls, meth,
                                lambda f, n=name: _counter(n, f)))
    optional_missing = []
    sabotage = float(os.environ.get("AEGIS_BENCH_SABOTAGE_MS") or 0)
    mod, cls, meth, name = PAINT
    try:
        installed.append(_patch(mod, cls, meth, lambda f: _span(
            name, _sabotaged(sabotage, f) if sabotage else f)))
    except ProbeError:
        if sabotage:
            raise
        optional_missing.append(f"{cls}.{meth}")
    _SINK.write({"k": "hooks", "pid": os.getpid(), "installed": installed,
                 "optional_missing": optional_missing})
    gc.callbacks.append(_gc_cb)
    threading.Thread(target=_sampler, name="aegis-bench-probe",
                     daemon=True).start()
```

`_patch` for the optional pane hook imports `aegis.tui.pane` before `aegis.cli`. That is an ordinary import of the same module the CLI imports next, so the patched class is the one the app uses.

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/bench/test_probe.py -q`
Expected: PASS.

- [x] **Step 5: Prove the probe runs inside the real daemon**

Extend `tests/bench/test_e2e.py::test_markers_reach_the_terminal` with, before teardown:

```python
        recs = read_jsonl(tmp_path / "probe.jsonl")
        assert any(r["k"] == "tick" for r in recs)
        gate = [r for r in recs if r["k"] == "gate"][-1]
        assert gate["sync"] is True and gate["headless"] is False
        assert gate["aegis_file"] == target.aegis_file
```

Run: `AEGIS_BENCH_E2E=1 uv run pytest tests/bench/test_e2e.py -q`
Expected: PASS. Then break it on purpose: temporarily change `REQUIRED`'s first method name to `_on_timer_updatex`, rerun, and confirm the test fails with the daemon's `ProbeError` in the message; restore and confirm `cmp` shows the file matches `git show HEAD:` plus this task's diff.

- [x] **Step 6: Commit**

```bash
git commit -m "feat(bench): in-process probe for frame spans, loop lag, GC and memory" -- src/aegis/bench/probe.py tests/bench/test_probe.py tests/bench/test_e2e.py
```

---

### Task 5: metrics and summaries

**Files:**
- Create: `src/aegis/bench/metrics.py`
- Test: `tests/bench/test_metrics.py`

**Interfaces:**
- Consumes: record shapes from Tasks 2-4, plus scenario events (Task 6): `{"k":"window","start_ns","end_ns"}`, `{"k":"metric","name","value"}`, `{"k":"sample_ms","name","value"}`, `{"k":"gate","name","ok","detail"}`, `{"k":"expect_markers","client"}`, `{"k":"client_ready","client","t_ns"}`.
- Produces:
  - `MetricSpec(unit: str, better: str = "lower", floor: float = 0.0, kind: str = "timing")`
  - `METRICS: dict[str, MetricSpec]` (every name below)
  - `pct(values: list[float], q: float) -> float | None` (nearest rank)
  - `repeat_metrics(rep_dir: Path) -> tuple[dict[str, float], list[dict]]` (metrics, gates `{"name","ok","detail"}`)
  - `summarize(run_id: str, fingerprint: dict, results: dict[str, dict]) -> dict` where `results[name] = {"status", "reason", "repeats": [metrics], "gates": [[gates]]}`; adds `"median"` per scenario; schema `{"schema": 1, "run_id", "fingerprint", "scenarios"}`.

Metric names (all in `METRICS`): `latency.marker_ms.{p50,p95,p99,max}`, `latency.marker_b_ms.{p50,p95,max}`, `latency.echo_ms.{p50,p95,max}`, `resize.first_frame_ms.{p50,max}`, `resize.settle_ms.{p50,max}`, `sidebar.first_frame_ms.{p50,max}`, `sidebar.settle_ms.{p50,max}`, `render.tick_ms.{p50,p95,p99,max}`, `render.layout_ms.p95`, `render.compose_ms.p95`, `render.display_ms.p95`, `render.paint_ms.p50`, `render.ticks_per_s`, `render.frames_per_s`, `render.bytes_per_frame.p50`, `render.height_calls`, `render.render_lines_calls`, `loop.lag_ms.{p50,p99,max}`, `loop.stalls_16_per_min`, `loop.stalls_50_per_min`, `loop.stalls_100_per_min`, `gc.pause_total_ms`, `gc.pause_max_ms`, `cpu.daemon_s_per_s`, `cpu.client_s_per_s`, `mem.rss_peak_mb`, `mem.rss_end_mb`, `mem.uss_peak_mb`, `mem.rss_growth_mb_per_1k_lines`, `startup.daemon_boot_ms`, `startup.first_frame_ms`, `startup.ready_ms`, `startup.warm_first_frame_ms`.

- [x] **Step 1: Write the failing tests**

```python
# tests/bench/test_metrics.py
import json

from aegis.bench.metrics import METRICS, pct, repeat_metrics, summarize

MS = 1_000_000


def _w(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_pct_nearest_rank():
    assert pct([], 0.5) is None
    assert pct([3, 1, 2], 0.5) == 2
    assert pct(list(range(1, 101)), 0.99) == 99


def test_marker_latency_joins_emit_to_first_frame_in_window(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "window", "start_ns": 100 * MS, "end_ns": 1000 * MS},
        {"k": "expect_markers", "client": "a"}])
    _w(tmp_path / "emit.jsonl", [
        {"k": "marker", "marker": "«b0001»", "t_emit_ns": 50 * MS},
        {"k": "marker", "marker": "«b0002»", "t_emit_ns": 200 * MS},
        {"k": "marker", "marker": "«b0003»", "t_emit_ns": 300 * MS}])
    _w(tmp_path / "frames.jsonl", [
        {"k": "frame", "client": "a", "t_ns": 230 * MS, "nbytes": 10,
         "markers": ["«b0002»"]},
        {"k": "frame", "client": "a", "t_ns": 260 * MS, "nbytes": 30,
         "markers": ["«b0002»"]},
        {"k": "frame", "client": "a", "t_ns": 340 * MS, "nbytes": 20,
         "markers": ["«b0003»"]}])
    m, gates = repeat_metrics(tmp_path)
    assert m["latency.marker_ms.p50"] == 30.0
    assert m["latency.marker_ms.max"] == 40.0
    assert {g["name"]: g["ok"] for g in gates}["markers_lost"] is True


def test_lost_marker_fails_the_gate(tmp_path):
    _w(tmp_path / "events.jsonl", [{"k": "expect_markers", "client": "a"}])
    _w(tmp_path / "emit.jsonl", [
        {"k": "marker", "marker": "«b0001»", "t_emit_ns": 1}])
    _w(tmp_path / "frames.jsonl", [])
    _, gates = repeat_metrics(tmp_path)
    lost = next(g for g in gates if g["name"] == "markers_lost")
    assert lost["ok"] is False and "«b0001»" in lost["detail"]


def test_probe_ticks_lag_and_samples(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "window", "start_ns": 0, "end_ns": 60_000 * MS}])
    _w(tmp_path / "probe.jsonl", [
        {"k": "tick", "t0": 10 * MS, "dur_ns": 4 * MS, "layout": 2 * MS,
         "compose": 1 * MS, "display": 1 * MS, "n_height": 3,
         "n_render_lines": 5},
        {"k": "lag", "t_ns": 20 * MS, "lag_ns": 60 * MS},
        {"k": "lag", "t_ns": 30 * MS, "lag_ns": 1 * MS},
        {"k": "sample", "t_ns": 0, "cpu_s": 1.0, "rss": 100 * 2**20,
         "uss": 80 * 2**20, "n_height": 0, "n_render_lines": 0,
         "gc_ns_total": 0, "gc_ns_max": 0},
        {"k": "sample", "t_ns": 60_000 * MS, "cpu_s": 7.0,
         "rss": 120 * 2**20, "uss": 90 * 2**20, "n_height": 30,
         "n_render_lines": 50, "gc_ns_total": 5 * MS, "gc_ns_max": 2 * MS},
        {"k": "gate", "sync": True, "headless": False, "aegis_file": "x"}])
    _w(tmp_path / "emit.jsonl", [])
    _w(tmp_path / "frames.jsonl", [])
    m, gates = repeat_metrics(tmp_path)
    assert m["render.tick_ms.p50"] == 4.0
    assert m["loop.stalls_50_per_min"] == 1.0
    assert m["cpu.daemon_s_per_s"] == 0.1
    assert m["mem.rss_peak_mb"] == 120.0
    assert m["render.height_calls"] == 30
    assert m["gc.pause_max_ms"] == 2.0
    names = {g["name"]: g["ok"] for g in gates}
    assert names["probe_sync"] and names["not_headless"]


def test_every_emitted_metric_has_a_spec(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "metric", "name": "startup.first_frame_ms", "value": 5.0},
        {"k": "sample_ms", "name": "resize.first_frame_ms", "value": 7.0}])
    m, _ = repeat_metrics(tmp_path)
    assert set(m) <= set(METRICS)


def test_summarize_takes_median_over_repeats():
    s = summarize("r", {"host": "h"}, {"x": {
        "status": "ok", "reason": "", "gates": [[], [], []],
        "repeats": [{"a": 1.0}, {"a": 9.0}, {"a": 3.0}]}})
    assert s["scenarios"]["x"]["median"] == {"a": 3.0}
    assert s["schema"] == 1
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/bench/test_metrics.py -q`
Expected: FAIL, module missing.

- [x] **Step 3: Implement**

```python
# src/aegis/bench/metrics.py
"""Fold one repeat's JSONL into metrics and gates, and repeats into medians.

Everything is restricted to the scenario's measurement window, so boot and
history preload never leak into a streaming number. Gates are computed
here rather than in the scenario so a summary cannot claim a clean run
its raw files contradict.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from aegis.bench.records import read_jsonl

MB = 2**20
NS_MS = 1_000_000


@dataclass(frozen=True)
class MetricSpec:
    unit: str
    better: str = "lower"
    floor: float = 0.0
    kind: str = "timing"


def _t(floor: float) -> MetricSpec:
    return MetricSpec("ms", floor=floor)


METRICS: dict[str, MetricSpec] = {
    **{f"latency.marker_ms.{q}": _t(2.0) for q in ("p50", "p95", "p99", "max")},
    **{f"latency.marker_b_ms.{q}": _t(2.0) for q in ("p50", "p95", "max")},
    **{f"latency.echo_ms.{q}": _t(2.0) for q in ("p50", "p95", "max")},
    **{f"{g}.{m}.{q}": _t(5.0) for g in ("resize", "sidebar")
       for m in ("first_frame_ms", "settle_ms") for q in ("p50", "max")},
    **{f"render.tick_ms.{q}": _t(0.5) for q in ("p50", "p95", "p99", "max")},
    "render.layout_ms.p95": _t(0.5),
    "render.compose_ms.p95": _t(0.5),
    "render.display_ms.p95": _t(0.5),
    "render.paint_ms.p50": _t(0.5),
    "render.ticks_per_s": MetricSpec("/s", floor=2.0, kind="resource"),
    "render.frames_per_s": MetricSpec("/s", floor=2.0, kind="resource"),
    "render.bytes_per_frame.p50": MetricSpec("B", floor=200, kind="resource"),
    "render.height_calls": MetricSpec("calls", kind="count"),
    "render.render_lines_calls": MetricSpec("calls", kind="count"),
    **{f"loop.lag_ms.{q}": _t(1.0) for q in ("p50", "p99", "max")},
    **{f"loop.stalls_{n}_per_min": MetricSpec("/min", floor=1.0,
                                             kind="resource")
       for n in (16, 50, 100)},
    "gc.pause_total_ms": _t(5.0),
    "gc.pause_max_ms": _t(2.0),
    "cpu.daemon_s_per_s": MetricSpec("s/s", floor=0.02, kind="resource"),
    "cpu.client_s_per_s": MetricSpec("s/s", floor=0.02, kind="resource"),
    "mem.rss_peak_mb": MetricSpec("MB", floor=2.0, kind="resource"),
    "mem.rss_end_mb": MetricSpec("MB", floor=2.0, kind="resource"),
    "mem.uss_peak_mb": MetricSpec("MB", floor=2.0, kind="resource"),
    "mem.rss_growth_mb_per_1k_lines": MetricSpec("MB", floor=0.5,
                                                 kind="resource"),
    "startup.daemon_boot_ms": _t(50.0),
    "startup.first_frame_ms": _t(50.0),
    "startup.ready_ms": _t(50.0),
    "startup.warm_first_frame_ms": _t(20.0),
}


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def _dist(out: dict, prefix: str, values: list[float],
          qs: tuple[str, ...]) -> None:
    if not values:
        return
    for q in qs:
        v = max(values) if q == "max" else pct(values, int(q[1:]) / 100)
        name = f"{prefix}.{q}"
        if name in METRICS:
            out[name] = round(float(v), 3)


def repeat_metrics(rep_dir: Path) -> tuple[dict[str, float], list[dict]]:
    rep_dir = Path(rep_dir)
    events = read_jsonl(rep_dir / "events.jsonl")
    emit = read_jsonl(rep_dir / "emit.jsonl")
    frames = read_jsonl(rep_dir / "frames.jsonl")
    probe = read_jsonl(rep_dir / "probe.jsonl")
    out: dict[str, float] = {}
    gates: list[dict] = []

    windows = [e for e in events if e["k"] == "window"]
    start = windows[-1]["start_ns"] if windows else 0
    end = windows[-1]["end_ns"] if windows else float("inf")
    inside = lambda t: start <= t <= end  # noqa: E731

    ready = {e["client"]: e["t_ns"] for e in events
             if e["k"] == "client_ready"}
    for exp in (e for e in events if e["k"] == "expect_markers"):
        client = exp["client"]
        since = max(start, ready.get(client, 0))
        first_seen: dict[str, int] = {}
        for f in frames:
            if f["k"] == "frame" and f["client"] == client:
                for m in f["markers"]:
                    first_seen.setdefault(m, f["t_ns"])
        lat, lost = [], []
        for r in emit:
            if r["k"] != "marker" or not (since <= r["t_emit_ns"] <= end):
                continue
            seen = first_seen.get(r["marker"])
            if seen is None:
                lost.append(r["marker"])
            else:
                lat.append((seen - r["t_emit_ns"]) / NS_MS)
        prefix = ("latency.marker_ms" if client == "a"
                  else f"latency.marker_{client}_ms")
        _dist(out, prefix, lat, ("p50", "p95", "p99", "max"))
        gates.append({"name": "markers_lost" if client == "a"
                      else f"markers_lost_{client}", "ok": not lost,
                      "detail": f"{len(lost)} of {len(lat) + len(lost)} lost"
                                + (f": {lost[:5]}" if lost else "")})

    fa = [f for f in frames if f["k"] == "frame" and f["client"] == "a"
          and inside(f["t_ns"])]
    span_s = None
    if windows:
        span_s = (end - start) / 1e9
    if fa:
        _dist(out, "render.bytes_per_frame", [f["nbytes"] for f in fa],
              ("p50",))
        if span_s:
            out["render.frames_per_s"] = round(len(fa) / span_s, 3)

    ticks = [r for r in probe if r["k"] == "tick" and inside(r["t0"])]
    if ticks:
        _dist(out, "render.tick_ms", [r["dur_ns"] / NS_MS for r in ticks],
              ("p50", "p95", "p99", "max"))
        for part in ("layout", "compose", "display"):
            _dist(out, f"render.{part}_ms",
                  [r[part] / NS_MS for r in ticks], ("p95",))
        if span_s:
            out["render.ticks_per_s"] = round(len(ticks) / span_s, 3)
    paints = [r["dur_ns"] / NS_MS for r in probe
              if r["k"] == "paint" and inside(r["t0"])]
    _dist(out, "render.paint_ms", paints, ("p50",))

    lags = [r["lag_ns"] / NS_MS for r in probe
            if r["k"] == "lag" and inside(r["t_ns"])]
    if lags:
        _dist(out, "loop.lag_ms", lags, ("p50", "p99", "max"))
        minutes = (span_s or (len(lags) * 0.005)) / 60
        for n in (16, 50, 100):
            out[f"loop.stalls_{n}_per_min"] = round(
                sum(1 for v in lags if v > n) / minutes, 3)

    samples = [r for r in probe if r["k"] == "sample" and inside(r["t_ns"])]
    if len(samples) >= 2:
        a, b = samples[0], samples[-1]
        dt = (b["t_ns"] - a["t_ns"]) / 1e9
        if dt > 0:
            out["cpu.daemon_s_per_s"] = round((b["cpu_s"] - a["cpu_s"]) / dt, 4)
        out["render.height_calls"] = b["n_height"] - a["n_height"]
        out["render.render_lines_calls"] = (b["n_render_lines"]
                                            - a["n_render_lines"])
        out["gc.pause_total_ms"] = round(
            (b["gc_ns_total"] - a["gc_ns_total"]) / NS_MS, 3)
        out["gc.pause_max_ms"] = round(b["gc_ns_max"] / NS_MS, 3)
    rss = [s["rss"] for s in samples if s.get("rss")]
    if rss:
        out["mem.rss_peak_mb"] = round(max(rss) / MB, 2)
        out["mem.rss_end_mb"] = round(rss[-1] / MB, 2)
    uss = [s["uss"] for s in samples if s.get("uss")]
    if uss:
        out["mem.uss_peak_mb"] = round(max(uss) / MB, 2)

    procs = [f for f in frames if f["k"] == "proc" and f["client"] == "a"
             and inside(f["t_ns"])]
    if len(procs) >= 2:
        dt = (procs[-1]["t_ns"] - procs[0]["t_ns"]) / 1e9
        if dt > 0:
            out["cpu.client_s_per_s"] = round(
                (procs[-1]["cpu_s"] - procs[0]["cpu_s"]) / dt, 4)

    grouped: dict[str, list[float]] = {}
    for e in events:
        if e["k"] == "metric" and e["name"] in METRICS:
            out[e["name"]] = e["value"]
        elif e["k"] == "sample_ms":
            grouped.setdefault(e["name"], []).append(e["value"])
    for name, values in grouped.items():
        _dist(out, name, values, ("p50", "p95", "max"))

    gate_recs = [r for r in probe if r["k"] == "gate"]
    if gate_recs:
        g = gate_recs[-1]
        gates.append({"name": "probe_sync", "ok": g["sync"],
                      "detail": "app._sync_available"})
        gates.append({"name": "not_headless", "ok": not g["headless"],
                      "detail": "app.is_headless"})
    gates.extend({"name": e["name"], "ok": e["ok"], "detail": e["detail"]}
                 for e in events if e["k"] == "gate")
    return out, gates


def summarize(run_id: str, fingerprint: dict,
              results: dict[str, dict]) -> dict:
    scenarios = {}
    for name, res in results.items():
        keys = sorted({k for rep in res["repeats"] for k in rep})
        median = {k: round(statistics.median(
            [rep[k] for rep in res["repeats"] if k in rep]), 4) for k in keys}
        scenarios[name] = {**res, "median": median}
    return {"schema": 1, "run_id": run_id, "fingerprint": fingerprint,
            "scenarios": scenarios}
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/bench/test_metrics.py -q`
Expected: PASS. If `test_every_emitted_metric_has_a_spec` fails, the `_dist` filter or a spec name is wrong; fix the name, never loosen the assertion.

- [x] **Step 5: Commit**

```bash
git commit -m "feat(bench): fold raw records into windowed metrics and gates" -- src/aegis/bench/metrics.py tests/bench/test_metrics.py
```

---

### Task 6: scenario context, the first scenarios, runner and `aegis bench run`

**Files:**
- Create: `src/aegis/bench/scenarios.py`, `src/aegis/bench/runner.py`, `src/aegis/cli_bench.py`
- Modify: `src/aegis/cli.py` (after the `_comms_app` registration)
- Test: `tests/bench/test_runner.py`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces:
  - `ScenarioContext(rep_dir: Path, target: Target, *, cols: int, rows: int, speed: float, sabotage_ms: int, profile: bool, keep: bool)` with `boot(script, *, default_agent="bench") -> Rig`, `attach(label: str) -> Rig`, `send_prompt(rig, word, *, timeout_s=20) -> None`, `wait_turns(n: int, *, timeout_s: float) -> None` (waits until at least n `turn_end` records exist), `window()` (context manager), `pump_for(seconds)`, `metric(name, value)`, `sample(name, value)`, `gate(name, ok, detail)`, `expect_markers(client="a")`, `wait_frame_after(rig, t_ns, timeout_s=10) -> int | None`, `wait_quiet(rig, quiet_ms=300, timeout_s=20) -> int`, `close()`.
  - `Scenario(name: str, fn: Callable[[ScenarioContext], None], description: str, topologies: tuple[str, ...] = ("daemon", "in-process"))`
  - `SCENARIOS: dict[str, Scenario]`, `DEFAULT: list[str]`, `QUICK: list[str]`
  - `RunOptions(scenarios: list[str], repeat: int = 3, cols: int = 120, rows: int = 40, target: str | None = None, speed: float = 1.0, profile: bool = False, sabotage_ms: int = 0, save: bool = False, out: Path | None = None, keep: bool = False)`
  - `runs_dir() -> Path` (`$AEGIS_BENCH_HOME/runs`, default `~/.aegis/bench/runs`), `history_dir() -> Path`, `fingerprint(target, opts) -> dict`, `run(opts, console) -> tuple[dict, Path]` (summary, run dir), `save_history(summary) -> Path`, `failed(summary) -> bool`.
  - CLI: `aegis bench run [--scenario/-s NAME ...] [--quick] [--repeat N] [--size COLSxROWS] [--target T] [--speed X] [--profile] [--sabotage MS] [--save] [--out DIR] [--keep]`, `aegis bench list`.

- [x] **Step 1: Write the failing unit tests**

```python
# tests/bench/test_runner.py
from aegis.bench.runner import failed, fingerprint, runs_dir
from aegis.bench.scenarios import DEFAULT, QUICK, SCENARIOS


def test_scenario_sets_are_registered():
    assert set(QUICK) <= set(DEFAULT) <= set(SCENARIOS)
    assert "soak" in SCENARIOS and "soak" not in DEFAULT
    assert QUICK == ["startup", "claude-blocks", "acp-stream", "resize"]


def test_runs_dir_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_BENCH_HOME", str(tmp_path))
    assert runs_dir() == tmp_path / "runs"


def test_failed_counts_gate_failures_and_failed_status():
    ok = {"scenarios": {"a": {"status": "ok", "gates": [[{"ok": True}]]}}}
    bad_gate = {"scenarios": {"a": {"status": "ok",
                                    "gates": [[{"ok": False}]]}}}
    bad = {"scenarios": {"a": {"status": "failed", "gates": []}}}
    skipped = {"scenarios": {"a": {"status": "skipped", "gates": []}}}
    assert not failed(ok) and failed(bad_gate) and failed(bad)
    assert not failed(skipped)
```

- [x] **Step 2: Implement `scenarios.py`** with the context and these scenarios: `startup`, `claude-blocks` (synthetic until Task 9 swaps in the fixture), `acp-stream` (depends on Task 7's fake; register it now, it fails until Task 7 lands, so run only `startup` and `claude-blocks` in this task), `deep-stream`, `resize`.

```python
# src/aegis/bench/scenarios.py
"""Scenario timelines. Each drives aegis the way an operator does.

A scenario never computes metrics. It writes what it did and when into
``events.jsonl`` (the window, resize times, echo samples) and
``metrics.repeat_metrics`` folds that together with the rig's frames, the
fake agents' emits and the probe.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError, ScenarioSkipped
from aegis.bench.launcher import Target
from aegis.bench.records import Recorder, read_jsonl
from aegis.bench.rig import Rig, pump, wait_until
from aegis.bench.script import (
    acp_chunks, make_script, synthetic_blocks, synthetic_fill)
from aegis.bench.world import (
    World, build_world, client_argv, start_daemon, teardown)

READY_TEXT = "type a message"
F3 = b"\x1bOR"
CTRL_T = b"\x14"


class ScenarioContext:
    def __init__(self, rep_dir: Path, target: Target, *, cols: int,
                 rows: int, speed: float, sabotage_ms: int, profile: bool,
                 keep: bool) -> None:
        self.rep_dir = Path(rep_dir)
        self.target = target
        self.cols, self.rows, self.speed = cols, rows, speed
        self.sabotage_ms, self.profile, self.keep = sabotage_ms, profile, keep
        self.events = Recorder(self.rep_dir / "events.jsonl")
        self.frames = Recorder(self.rep_dir / "frames.jsonl")
        self.world: World | None = None
        self.rigs: list[Rig] = []

    # --- recording -----------------------------------------------------
    def metric(self, name: str, value: float) -> None:
        self.events.write({"k": "metric", "name": name,
                           "value": round(float(value), 3)})

    def sample(self, name: str, value: float) -> None:
        self.events.write({"k": "sample_ms", "name": name,
                           "value": round(float(value), 3)})

    def gate(self, name: str, ok: bool, detail: str = "") -> None:
        self.events.write({"k": "gate", "name": name, "ok": bool(ok),
                           "detail": detail})

    def expect_markers(self, client: str = "a") -> None:
        self.events.write({"k": "expect_markers", "client": client})

    @contextlib.contextmanager
    def window(self):
        start = time.monotonic_ns()
        try:
            yield
        finally:
            self.events.write({"k": "window", "start_ns": start,
                               "end_ns": time.monotonic_ns()})

    # --- world ---------------------------------------------------------
    def _wrap(self) -> list[str] | None:
        if not self.profile:
            return None
        return ["uvx", "py-spy", "record", "--format", "speedscope",
                "--output", str(self.rep_dir / "profile.speedscope.json"),
                "--"]

    def boot(self, script: dict, *, default_agent: str = "bench") -> Rig:
        script = dict(script, speed=self.speed)
        self.world = build_world(self.rep_dir, self.target, script=script,
                                 default_agent=default_agent,
                                 sabotage_ms=self.sabotage_ms)
        if self.target.topology == "daemon":
            self.metric("startup.daemon_boot_ms",
                        start_daemon(self.world, wrap=self._wrap()))
        return self.attach("a")

    def attach(self, label: str) -> Rig:
        assert self.world is not None
        wrap = self._wrap() if self.target.topology == "in-process" else None
        rig = Rig(client_argv(self.world, view=f"bench-{label}", wrap=wrap),
                  cwd=self.world.root, env=self.world.env, cols=self.cols,
                  rows=self.rows, label=label, recorder=self.frames)
        rig.start()
        self.rigs.append(rig)
        if not wait_until(self.rigs, lambda: rig.first_frame_ns is not None,
                          90):
            raise BenchError(f"client {label} drew no frame in 90 s "
                             f"(sync negotiated: {rig.saw_sync})")
        if not wait_until(self.rigs, lambda: rig.contains(READY_TEXT), 60):
            raise BenchError(f"client {label} never showed {READY_TEXT!r}")
        now = time.monotonic_ns()
        if label == "a":
            self.metric("startup.first_frame_ms",
                        (rig.first_frame_ns - rig.t_start_ns) / 1e6)
            self.metric("startup.ready_ms", (now - rig.t_start_ns) / 1e6)
        self.events.write({"k": "client_ready", "client": label,
                           "t_ns": now})
        return rig

    def _emits(self, kind: str) -> list[dict]:
        return [r for r in read_jsonl(self.rep_dir / "emit.jsonl")
                if r["k"] == kind]

    def send_prompt(self, rig: Rig, word: str, *,
                    timeout_s: float = 20) -> None:
        before = len(self._emits("prompt"))
        rig.write(word.encode())
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rig.write(b"\r")
            if wait_until(self.rigs,
                          lambda: len(self._emits("prompt")) > before, 3):
                return
        raise BenchError(f"prompt {word!r} never reached the fake agent")

    def wait_turns(self, n: int, *, timeout_s: float) -> None:
        if not wait_until(self.rigs, lambda: len(self._emits("turn_end")) >= n,
                          timeout_s):
            raise BenchError(f"fewer than {n} turns finished in {timeout_s}s")

    def pump_for(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            pump(self.rigs)

    def wait_frame_after(self, rig: Rig, t_ns: int,
                         timeout_s: float = 10) -> int | None:
        ok = wait_until(self.rigs, lambda: (rig.last_frame_ns or 0) > t_ns,
                        timeout_s)
        return rig.last_frame_ns if ok else None

    def wait_quiet(self, rig: Rig, quiet_ms: float = 300,
                   timeout_s: float = 20) -> int:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            pump(self.rigs)
            last = rig.last_frame_ns or 0
            if time.monotonic_ns() - last > quiet_ms * 1e6:
                return last
        return rig.last_frame_ns or 0

    def close(self) -> None:
        for rig in self.rigs:
            self.gate(f"sync_seen_{rig.label}", rig.saw_sync,
                      "rig saw \\e[?2026h")
        # Let the probe's 1 s sampler flush after the window closes.
        self.pump_for(1.2)
        for rig in self.rigs:
            rig.close()
        if self.world is not None:
            teardown(self.world, keep=self.keep)
        self.events.close()
        self.frames.close()


@dataclass(frozen=True)
class Scenario:
    name: str
    fn: Callable[[ScenarioContext], None]
    description: str
    topologies: tuple[str, ...] = ("daemon", "in-process")


def _fill_script(**prompts) -> dict:
    return make_script({"fill": synthetic_fill(150), **prompts})


def startup(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({}))
    if ctx.target.topology != "daemon":
        return
    rig.close()
    ctx.rigs.remove(rig)
    t0 = time.monotonic_ns()
    warm = ctx.attach("a2")
    ctx.metric("startup.warm_first_frame_ms",
               (warm.first_frame_ns - t0) / 1e6)


def claude_blocks(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": synthetic_blocks(120, 50)}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=180)
        ctx.pump_for(1.0)


def deep_stream(ctx: ScenarioContext) -> None:
    rig = ctx.boot(_fill_script(go=synthetic_blocks(80, 50)))
    ctx.send_prompt(rig, "fill")
    ctx.wait_turns(1, timeout_s=180)
    ctx.wait_quiet(rig, 500)
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(2, timeout_s=180)
        ctx.pump_for(1.0)


def resize(ctx: ScenarioContext) -> None:
    rig = ctx.boot(_fill_script())
    ctx.send_prompt(rig, "fill")
    ctx.wait_turns(1, timeout_s=180)
    ctx.wait_quiet(rig, 500)
    with ctx.window():
        for i in range(5):
            cols = ctx.cols - 20 if i % 2 == 0 else ctx.cols
            t = rig.resize(cols, ctx.rows)
            first = ctx.wait_frame_after(rig, t)
            settle = ctx.wait_quiet(rig, 300)
            if first is None:
                raise BenchError("no frame after resize")
            ctx.sample("resize.first_frame_ms", (first - t) / 1e6)
            ctx.sample("resize.settle_ms", (settle - t) / 1e6)
        for _ in range(5):
            t = rig.write(F3)
            first = ctx.wait_frame_after(rig, t)
            settle = ctx.wait_quiet(rig, 300)
            if first is None:
                raise BenchError("no frame after sidebar toggle")
            ctx.sample("sidebar.first_frame_ms", (first - t) / 1e6)
            ctx.sample("sidebar.settle_ms", (settle - t) / 1e6)


SCENARIOS: dict[str, Scenario] = {s.name: s for s in (
    Scenario("startup", startup, "cold boot, first frame, warm re-attach"),
    Scenario("claude-blocks", claude_blocks,
             "whole text blocks, as aegis receives claude today"),
    Scenario("deep-stream", deep_stream, "stream after ~300 mounted blocks"),
    Scenario("resize", resize, "resizes and sidebar toggles at ~300 blocks"),
)}
DEFAULT = ["startup", "claude-blocks", "deep-stream", "resize"]
QUICK = ["startup", "claude-blocks", "resize"]
```

`DEFAULT` and `QUICK` grow in Tasks 7 and 8; the final values are the ones `test_scenario_sets_are_registered` asserts, so that test stays red until Task 8. Mark it `xfail(strict=True)` in this task with reason "scenario sets complete in Task 8" and remove the mark there.

- [x] **Step 3: Implement `runner.py`**

```python
# src/aegis/bench/runner.py
"""Run scenarios × repeats against a target and write the run directory."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import socket
import subprocess
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from aegis.bench import BenchError, ScenarioSkipped
from aegis.bench.launcher import Target, resolve_target
from aegis.bench.metrics import repeat_metrics, summarize
from aegis.bench.scenarios import SCENARIOS, ScenarioContext


@dataclass
class RunOptions:
    scenarios: list[str] = field(default_factory=list)
    repeat: int = 3
    cols: int = 120
    rows: int = 40
    target: str | None = None
    speed: float = 1.0
    profile: bool = False
    sabotage_ms: int = 0
    save: bool = False
    out: Path | None = None
    keep: bool = False


def runs_dir() -> Path:
    home = os.environ.get("AEGIS_BENCH_HOME")
    base = Path(home) if home else Path.home() / ".aegis" / "bench"
    return base / "runs"


def history_dir() -> Path:
    import aegis
    root = Path(aegis.__file__).resolve().parents[2]
    pyproject = root / "pyproject.toml"
    if not (pyproject.exists()
            and 'name = "aegis-harness"' in pyproject.read_text()):
        raise BenchError("--save needs aegis installed editable from its "
                         "checkout; bench/history lives in the repo")
    return root / "bench" / "history" / socket.gethostname()


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _git(aegis_file: str) -> tuple[str, bool]:
    root = Path(aegis_file).resolve().parents[2]
    if not (root / ".git").exists():
        return "", False
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short",
                          "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                            "--", "src"], capture_output=True, text=True)
    return sha.stdout.strip(), bool(dirty.stdout.strip())


def fingerprint(target: Target, opts: RunOptions) -> dict:
    cpu = next((line.split(":", 1)[1].strip()
                for line in _read("/proc/cpuinfo").splitlines()
                if line.startswith("model name")), platform.processor())
    sha, dirty = _git(target.aegis_file)
    import aegis.version
    return {
        "host": socket.gethostname(), "cpu": cpu, "cores": os.cpu_count(),
        "governor": _read(
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "size": f"{opts.cols}x{opts.rows}", "speed": opts.speed,
        "sabotage_ms": opts.sabotage_ms, "target": target.label,
        "topology": target.topology, "aegis_version": target.version,
        "aegis_build": target.build, "aegis_file": target.aegis_file,
        "git_sha": sha, "git_dirty": dirty, "python": target.python_version,
        "textual": target.textual, "rich": target.rich,
        "bench_build": aegis.version.BUILD,
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }


def failed(summary: dict) -> bool:
    for sc in summary["scenarios"].values():
        if sc["status"] == "failed":
            return True
        if any(not g["ok"] for rep in sc.get("gates", []) for g in rep):
            return True
    return False


def run(opts: RunOptions, console: Console) -> tuple[dict, Path]:
    target = resolve_target(opts.target)
    fp = fingerprint(target, opts)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    run_id = f"{stamp}-{target.label.replace('/', '_')}"
    run_dir = (opts.out or runs_dir()) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[bold]aegis bench[/] {run_id}  target {target.build} "
                  f"({target.topology})  -> {run_dir}")
    results: dict[str, dict] = {}
    for name in opts.scenarios:
        sc = SCENARIOS[name]
        res = {"status": "ok", "reason": "", "repeats": [], "gates": []}
        results[name] = res
        if target.topology not in sc.topologies:
            res.update(status="skipped",
                       reason=f"not applicable to {target.topology}")
            console.print(f"  {name}: skipped ({res['reason']})")
            continue
        for r in range(opts.repeat):
            rep_dir = run_dir / name / f"r{r}"
            ctx = ScenarioContext(rep_dir, target, cols=opts.cols,
                                  rows=opts.rows, speed=opts.speed,
                                  sabotage_ms=opts.sabotage_ms,
                                  profile=opts.profile, keep=opts.keep)
            try:
                sc.fn(ctx)
            except ScenarioSkipped as skip:
                res.update(status="skipped", reason=str(skip))
            except (BenchError, OSError) as exc:
                res.update(status="failed", reason=str(exc))
                (rep_dir / "error.txt").write_text(traceback.format_exc())
            finally:
                ctx.close()
            if res["status"] != "ok":
                console.print(f"  {name} r{r}: {res['status']} "
                              f"({res['reason'].splitlines()[0]})")
                break
            metrics, gates = repeat_metrics(rep_dir)
            res["repeats"].append(metrics)
            res["gates"].append(gates)
            bad = [g["name"] for g in gates if not g["ok"]]
            console.print(f"  {name} r{r}: {len(metrics)} metrics"
                          + (f", [red]gates failed: {bad}[/]" if bad else ""))
    summary = summarize(run_id, fp, results)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if opts.save:
        path = save_history(summary)
        console.print(f"saved {path}")
    return summary, run_dir


def save_history(summary: dict) -> Path:
    fp = summary["fingerprint"]
    base = history_dir()
    base.mkdir(parents=True, exist_ok=True)
    name = fp["aegis_version"]
    if fp["target"] == "current" and fp["git_sha"]:
        name += f"-{fp['git_sha']}" + ("-dirty" if fp["git_dirty"] else "")
    path = base / f"{name}.json"
    path.write_text(json.dumps(summary, indent=2))
    return path
```

- [x] **Step 4: Implement `cli_bench.py` (run + list) and register it**

```python
# src/aegis/cli_bench.py
"""``aegis bench``: measure rendering, latency, CPU and memory.

See ``know-how/benchmarking.md``.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Benchmark the TUI against a real daemon and client.",
                  no_args_is_help=True)
_console = Console()


def _size(value: str) -> tuple[int, int]:
    try:
        cols, rows = (int(p) for p in value.lower().split("x"))
    except ValueError as exc:
        raise typer.BadParameter("expected COLSxROWS, e.g. 120x40") from exc
    return cols, rows


@app.command("run")
def run_cmd(
    scenario: list[str] = typer.Option(None, "--scenario", "-s",
                                       help="Scenario name (repeatable)."),
    quick: bool = typer.Option(False, "--quick",
                               help="A fast subset, one repeat."),
    repeat: int = typer.Option(3, "--repeat", min=1),
    size: str = typer.Option("120x40", "--size", help="COLSxROWS."),
    target: str = typer.Option(None, "--target",
                               help="X.Y.Z from PyPI, or a python path."),
    speed: float = typer.Option(1.0, "--speed",
                                help="Replay speed factor."),
    profile: bool = typer.Option(False, "--profile",
                                 help="Record a py-spy speedscope profile."),
    sabotage: int = typer.Option(0, "--sabotage",
                                 help="Sleep MS in every streaming paint."),
    save: bool = typer.Option(False, "--save",
                              help="Copy the summary to bench/history."),
    out: Path = typer.Option(None, "--out", help="Runs directory."),
    keep: bool = typer.Option(False, "--keep",
                              help="Keep the /tmp worlds for inspection."),
) -> None:
    """Run scenarios and write a summary."""
    from aegis.bench import BenchError
    from aegis.bench.report import print_run, render_markdown
    from aegis.bench.runner import RunOptions, failed, run
    from aegis.bench.scenarios import DEFAULT, QUICK, SCENARIOS
    names = list(scenario or (QUICK if quick else DEFAULT))
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        raise typer.BadParameter(f"unknown scenario(s): {unknown}; "
                                 f"see `aegis bench list`")
    cols, rows = _size(size)
    opts = RunOptions(scenarios=names, repeat=1 if quick else repeat,
                      cols=cols, rows=rows, target=target, speed=speed,
                      profile=profile, sabotage_ms=sabotage, save=save,
                      out=out, keep=keep)
    try:
        summary, run_dir = run(opts, _console)
    except BenchError as exc:
        _console.print(f"[red]bench failed:[/] {exc}")
        raise typer.Exit(2) from exc
    print_run(summary, _console)
    (run_dir / "report.md").write_text(render_markdown(summary))
    raise typer.Exit(1 if failed(summary) else 0)


@app.command("list")
def list_cmd() -> None:
    """List scenarios and recent runs."""
    from rich.table import Table

    from aegis.bench.runner import runs_dir
    from aegis.bench.scenarios import DEFAULT, QUICK, SCENARIOS
    t = Table("scenario", "default", "quick", "description")
    for s in SCENARIOS.values():
        t.add_row(s.name, "yes" if s.name in DEFAULT else "",
                  "yes" if s.name in QUICK else "", s.description)
    _console.print(t)
    base = runs_dir()
    runs = sorted(base.glob("*/summary.json"))[-10:] if base.exists() else []
    for p in runs:
        _console.print(f"  {p.parent.name}")
```

In `src/aegis/cli.py`, after the `_comms_app` lines:

```python
from aegis.cli_bench import app as _bench_app  # noqa: E402
app.add_typer(_bench_app, name="bench")
```

`report.py` arrives in Task 7; in this task create it with `print_run(summary, console)` printing one rich table per scenario (metric, median, unit, repeats) and `render_markdown(summary) -> str` producing the same as a Markdown table, so `run` works end to end.

```python
# src/aegis/bench/report.py
"""Tables for run, compare and history, in the terminal and as Markdown."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from aegis.bench.metrics import METRICS


def _fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:,.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def print_run(summary: dict, console: Console) -> None:
    fp = summary["fingerprint"]
    console.print(f"[bold]{summary['run_id']}[/]  {fp['aegis_build']} "
                  f"{fp['topology']}  {fp['host']}  {fp['size']}")
    for name, sc in summary["scenarios"].items():
        title = f"{name} [{sc['status']}]" + (
            f" {sc['reason'].splitlines()[0]}" if sc["reason"] else "")
        t = Table("metric", "median", "unit", "repeats", title=title)
        for metric, value in sc.get("median", {}).items():
            reps = [rep.get(metric) for rep in sc["repeats"]]
            t.add_row(metric, _fmt(value), METRICS[metric].unit,
                      " ".join(_fmt(r) for r in reps))
        console.print(t)
        bad = sorted({g["name"] for rep in sc.get("gates", []) for g in rep
                      if not g["ok"]})
        if bad:
            console.print(f"  [red]failed gates: {', '.join(bad)}[/]")


def render_markdown(summary: dict) -> str:
    fp = summary["fingerprint"]
    lines = [f"# aegis bench {summary['run_id']}", "",
             f"{fp['aegis_build']} ({fp['topology']}) on {fp['host']}, "
             f"{fp['cpu']}, {fp['size']}, Textual {fp['textual']}", ""]
    for name, sc in summary["scenarios"].items():
        lines += [f"## {name}: {sc['status']}", ""]
        if sc["reason"]:
            lines += [sc["reason"].splitlines()[0], ""]
        if sc.get("median"):
            lines += ["| metric | median | unit |", "|---|---|---|"]
            lines += [f"| {m} | {_fmt(v)} | {METRICS[m].unit} |"
                      for m, v in sc["median"].items()]
            lines.append("")
    return "\n".join(lines)
```

- [x] **Step 5: Run unit tests and a real run**

Run: `uv run pytest tests/bench -q`
Expected: PASS (with the one strict xfail).

Run: `uv run aegis bench run -s startup -s claude-blocks --repeat 1`
Expected: exit 0; the table shows `latency.marker_ms.*`, `render.tick_ms.*`, `loop.lag_ms.*`, `cpu.daemon_s_per_s`, `mem.rss_peak_mb`, `startup.*`; no failed gates. Read the rc directly (`echo $?` as its own command), never through a pipe.

- [x] **Step 6: Break it on purpose**

Run: `uv run aegis bench run -s claude-blocks --repeat 1 --sabotage 40`, then compare `latency.marker_ms.p50` and `render.paint_ms.p50` with the plain run. Expected: both rise by at least 30 ms. If they do not, the sabotage does not reach `_paint_streaming` on this path; stop and fix the measurement before continuing.

- [x] **Step 7: Commit**

```bash
git commit -m "feat(bench): aegis bench run with startup, block, deep and resize scenarios" -- src/aegis/bench/scenarios.py src/aegis/bench/runner.py src/aegis/bench/report.py src/aegis/cli_bench.py src/aegis/cli.py tests/bench/test_runner.py
```

---

### Task 7: compare, history and the fake ACP agent

**Files:**
- Create: `src/aegis/bench/compare.py`, `src/aegis/bench/fake_acp.py`
- Modify: `src/aegis/bench/report.py` (add `print_compare`, `print_history`), `src/aegis/cli_bench.py` (add `compare`, `history`), `src/aegis/bench/scenarios.py` (add `acp-stream`, `typing`)
- Test: `tests/bench/test_compare.py`, `tests/bench/test_fake_acp.py`

**Interfaces:**
- Produces:
  - `CompareError(BenchError)`; `Row(metric: str, a: float | None, b: float | None, delta_pct: float | None, verdict: str)`
  - `verdict(spec: MetricSpec, a: list[float], b: list[float]) -> str` in `{"improved", "regressed", "noise", "same", "changed", "new", "gone"}`
  - `compare(a: dict, b: dict, *, force: bool = False) -> dict[str, list[Row]]` keyed by scenario
  - `load_summary(ref: str) -> dict` (a path to `summary.json`, a run dir, a run id under `runs_dir()`, or a history file)
  - `latest_release(host: str) -> dict | None` (highest `X.Y.Z` history file without a sha suffix)
  - CLI `aegis bench compare A [B] [--baseline latest-release] [--force]`, `aegis bench history [--metric M ...] [--scenario S]`

- [x] **Step 1: Failing tests**

```python
# tests/bench/test_compare.py
import pytest

from aegis.bench.compare import CompareError, compare, verdict
from aegis.bench.metrics import MetricSpec

T = MetricSpec("ms", floor=0.5)


def test_verdicts():
    assert verdict(T, [10, 10.2, 10.1], [12, 12.3, 12.1]) == "regressed"
    assert verdict(T, [12, 12.3, 12.1], [10, 10.2, 10.1]) == "improved"
    assert verdict(T, [10, 10.5, 10.2], [10.6, 10.9, 10.7]) == "noise"  # < 10%
    assert verdict(T, [1.0, 1.1], [1.3, 1.4]) == "noise"   # under the floor
    assert verdict(T, [10, 14], [13, 16]) == "noise"       # ranges overlap
    c = MetricSpec("calls", kind="count")
    assert verdict(c, [30, 30], [30, 30]) == "same"
    assert verdict(c, [30], [45]) == "changed"
    fps = MetricSpec("/s", better="higher", floor=2.0, kind="resource")
    assert verdict(fps, [30, 31], [50, 52]) == "improved"


def _s(host="h", size="120x40", reps=({"render.tick_ms.p50": 4.0},)):
    return {"fingerprint": {"host": host, "size": size},
            "scenarios": {"x": {"status": "ok", "repeats": list(reps)}}}


def test_refuses_different_hosts_unless_forced():
    with pytest.raises(CompareError):
        compare(_s(host="a"), _s(host="b"))
    assert compare(_s(host="a"), _s(host="b"), force=True)


def test_new_and_gone_metrics():
    rows = compare(_s(reps=({"render.tick_ms.p50": 4.0},)),
                   _s(reps=({"render.tick_ms.p95": 4.0},)))["x"]
    got = {r.metric: r.verdict for r in rows}
    assert got == {"render.tick_ms.p50": "gone", "render.tick_ms.p95": "new"}
```

```python
# tests/bench/test_fake_acp.py
"""The fake ACP agent speaks enough ACP v1 for aegis's AcpSession."""
import json
import sys

from aegis.bench.records import read_jsonl
from aegis.bench.script import acp_chunks, make_script
from aegis.config import Agent, GeminiCLI
from aegis.drivers.acp import AcpDriver
from aegis.events import AssistantText, Result


class _Driver(AcpDriver):
    BASE_CMD = [sys.executable, "-m", "aegis.bench.fake_acp"]


async def test_prompt_streams_marked_chunks(tmp_path, monkeypatch):
    script = tmp_path / "script.json"
    script.write_text(json.dumps(make_script({"go": acp_chunks(10, 1000)})))
    monkeypatch.setenv("AEGIS_BENCH_SCRIPT", str(script))
    monkeypatch.setenv("AEGIS_BENCH_EMIT", str(tmp_path / "emit.jsonl"))
    agent = Agent(slug="t", provider=GeminiCLI(model="x"))
    session = _Driver().session(agent, str(tmp_path), "", "h")
    await session.start()
    try:
        await session.send("go")
        events = [e async for e in session.events()]
    finally:
        await session.close()
    text = "".join(e.text for e in events if isinstance(e, AssistantText))
    assert text.count("«b") == 2
    assert isinstance(events[-1], Result)
    kinds = [r["k"] for r in read_jsonl(tmp_path / "emit.jsonl")]
    assert kinds.count("marker") == 2 and "turn_end" in kinds
```

Before writing `test_fake_acp.py`, read `tests/test_drivers_acp.py::test_acp_session_basic_round_trip` and copy exactly how it constructs the `Agent`, the driver and the session, and how it closes the session; the snippet above is the shape, the existing test is the authority for the constructor arguments.

- [x] **Step 2: Implement `compare.py`**

```python
# src/aegis/bench/compare.py
"""Per-metric verdicts between two summaries.

A change counts only when it is larger than 10% of the baseline, larger
than the metric's absolute floor, and the two runs' repeat ranges do not
overlap. All three, because each alone is fooled by something ordinary:
a relative threshold by tiny baselines, a floor by large ones, and range
separation by a single noisy repeat.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError
from aegis.bench.metrics import METRICS, MetricSpec
from aegis.bench.runner import history_dir, runs_dir

REL = 0.10


class CompareError(BenchError):
    pass


@dataclass(frozen=True)
class Row:
    metric: str
    a: float | None
    b: float | None
    delta_pct: float | None
    verdict: str


def verdict(spec: MetricSpec, a: list[float], b: list[float]) -> str:
    ma, mb = statistics.median(a), statistics.median(b)
    if spec.kind == "count":
        return "same" if ma == mb else "changed"
    diff = mb - ma
    if abs(diff) <= spec.floor:
        return "noise"
    if ma and abs(diff) / abs(ma) <= REL:
        return "noise"
    if min(b) <= max(a) and min(a) <= max(b):
        return "noise"
    worse = diff > 0 if spec.better == "lower" else diff < 0
    return "regressed" if worse else "improved"


def compare(a: dict, b: dict, *, force: bool = False) -> dict[str, list[Row]]:
    fa, fb = a["fingerprint"], b["fingerprint"]
    for key in ("host", "size"):
        if fa.get(key) != fb.get(key) and not force:
            raise CompareError(f"{key} differs ({fa.get(key)} vs "
                               f"{fb.get(key)}); pass --force to compare")
    out: dict[str, list[Row]] = {}
    for name in sorted(set(a["scenarios"]) & set(b["scenarios"])):
        ra, rb = a["scenarios"][name]["repeats"], b["scenarios"][name]["repeats"]
        metrics = sorted({k for rep in ra + rb for k in rep})
        rows = []
        for m in metrics:
            va = [rep[m] for rep in ra if m in rep]
            vb = [rep[m] for rep in rb if m in rep]
            if not va or not vb:
                rows.append(Row(m, statistics.median(va) if va else None,
                                statistics.median(vb) if vb else None, None,
                                "new" if vb else "gone"))
                continue
            ma, mb = statistics.median(va), statistics.median(vb)
            delta = (mb - ma) / ma * 100 if ma else None
            rows.append(Row(m, ma, mb, delta,
                            verdict(METRICS.get(m, MetricSpec("?")), va, vb)))
        out[name] = rows
    return out


def load_summary(ref: str) -> dict:
    p = Path(ref).expanduser()
    for cand in (p, p / "summary.json", runs_dir() / ref / "summary.json"):
        if cand.is_file():
            return json.loads(cand.read_text())
    raise BenchError(f"no summary found for {ref!r}")


def _version_key(path: Path) -> tuple[int, ...] | None:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", path.stem)
    return tuple(int(x) for x in m.groups()) if m else None


def latest_release(host: str | None = None) -> dict | None:
    base = history_dir() if host is None else history_dir().parent / host
    files = [p for p in base.glob("*.json") if _version_key(p)]
    if not files:
        return None
    return json.loads(max(files, key=_version_key).read_text())
```

- [x] **Step 3: Implement `fake_acp.py`**

```python
# src/aegis/bench/fake_acp.py
"""A stand-in for ``lovelaice-acp``: an ACP v1 agent replaying chunks.

This is the only path where aegis renders a reply chunk by chunk today,
so it is where per-delta rendering cost shows up.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import acp
from acp.schema import AgentMessageChunk, TextContentBlock

from aegis.bench.frames import marker
from aegis.bench.records import MarkerSeq, Recorder
from aegis.bench.script import route


class BenchAgent(acp.Agent):
    def __init__(self) -> None:
        emit = Path(os.environ["AEGIS_BENCH_EMIT"])
        self._script = json.loads(
            Path(os.environ["AEGIS_BENCH_SCRIPT"]).read_text())
        self._emit = Recorder(emit)
        self._seq = MarkerSeq(emit.parent / "marker.seq")
        self._pid = os.getpid()

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": False,
                               "mcpCapabilities": {"http": True}},
            agentInfo={"name": "aegis-bench", "version": "1"})

    async def new_session(self, cwd, mcp_servers=None,
                          additional_directories=None, **kw):
        return acp.NewSessionResponse(sessionId=f"bench-{self._pid}")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        text = " ".join(getattr(b, "text", "") or "" for b in prompt)
        self._emit.write({"k": "prompt", "pid": self._pid,
                          "t_ns": time.monotonic_ns(),
                          "word": (text.split() or [""])[0].lower()})
        speed = float(self._script.get("speed") or 1.0)
        n = 0
        for step in route(self._script, text):
            if "chunk" not in step:
                continue
            delay = float(step.get("dt_ms", 0.0)) / speed
            if delay > 0:
                await asyncio.sleep(delay / 1000.0)
            chunk, m = step["chunk"], None
            if step.get("mark", True):
                m = marker(self._seq.next())
                chunk = f"{chunk}{m} "
            t = time.monotonic_ns()
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    content=TextContentBlock(text=chunk, type="text"),
                    sessionUpdate="agent_message_chunk"))
            n += 1
            if m is not None:
                self._emit.write({"k": "marker", "pid": self._pid,
                                  "marker": m, "t_emit_ns": t})
        self._emit.write({"k": "turn_end", "pid": self._pid,
                          "t_ns": time.monotonic_ns(), "lines": n})
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


def main() -> None:
    asyncio.run(acp.run_agent(BenchAgent()))


if __name__ == "__main__":
    main()
```

`route` falls back to a claude-shaped ack for unknown prompts; `prompt` skips steps without `chunk`, so an unknown prompt ends the turn with no text. That is fine for the bench, which only sends known words.

- [x] **Step 4: Add the ACP scenarios** to `scenarios.py` and register them:

```python
def acp_stream(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": acp_chunks(500, 50)}),
                   default_agent="bench-acp")
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=120)
        ctx.pump_for(1.0)


def typing(ctx: ScenarioContext) -> None:
    import random
    rig = ctx.boot(make_script({"go": acp_chunks(900, 50)}),
                   default_agent="bench-acp")
    typed = "".join(random.Random(7).choice("bcdfghjkmnpqrstvwxz")
                    for _ in range(200))
    pending: list[tuple[str, int]] = []

    def on_frame(frame) -> None:
        while pending and pending[0][0] in frame.text:
            tail, t_sent = pending.pop(0)
            ctx.sample("latency.echo_ms", (frame.t_ns - t_sent) / 1e6)

    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.pump_for(1.0)
        rig.listeners.append(on_frame)
        for i, ch in enumerate(typed):
            t = rig.write(ch.encode())
            pending.append((typed[max(0, i - 7):i + 1], t))
            ctx.pump_for(0.05)
        ctx.pump_for(1.0)
        rig.listeners.remove(on_frame)
    ctx.gate("echo_lost", not pending, f"{len(pending)} keystrokes never echoed")
```

The echo detector matches the last eight typed characters in a frame, because Textual rewrites the input line as one span. If the gate fails with most keystrokes lost, dump a frame after a keystroke and adjust the detector to what Textual actually writes; do not relax the gate.

Update `SCENARIOS`, `DEFAULT = ["startup", "claude-blocks", "acp-stream", "deep-stream", "resize", "typing"]`, `QUICK = ["startup", "claude-blocks", "acp-stream", "resize"]`.

- [x] **Step 5: Verify the lovelaice YAML shape the world writes**

Run: `uv run aegis bench run -s acp-stream --repeat 1 --keep`
Expected: exit 0 with `latency.marker_ms.*`. If the daemon rejects `provider: lovelaice` in `.aegis.yaml`, read `src/aegis/config/yaml_loader.py` for how agent entries become `Agent` objects, fix `_CONFIG` in `world.py`, and rerun. The kept world is under `/tmp/aegis-bench-*`; remove it after reading.

- [x] **Step 6: Add `compare` and `history`** to `report.py` and `cli_bench.py`

```python
# report.py additions
_COLOR = {"regressed": "red", "improved": "green", "changed": "yellow",
          "new": "cyan", "gone": "cyan"}


def print_compare(rows_by_scenario, console: Console, *,
                  a_label: str, b_label: str, all_rows: bool = False) -> None:
    for name, rows in rows_by_scenario.items():
        t = Table("metric", a_label, b_label, "Δ%", "verdict", title=name)
        for r in rows:
            if not all_rows and r.verdict in ("noise", "same"):
                continue
            color = _COLOR.get(r.verdict, "")
            t.add_row(r.metric, _fmt(r.a), _fmt(r.b),
                      "-" if r.delta_pct is None else f"{r.delta_pct:+.1f}",
                      f"[{color}]{r.verdict}[/]" if color else r.verdict)
        console.print(t)


def print_history(summaries: list[dict], metrics: list[str], scenario: str,
                  console: Console) -> None:
    t = Table("version", "topology", *metrics, title=scenario)
    for s in summaries:
        med = s["scenarios"].get(scenario, {}).get("median", {})
        t.add_row(s["fingerprint"]["aegis_build"],
                  s["fingerprint"]["topology"],
                  *(_fmt(med.get(m)) for m in metrics))
    console.print(t)
```

```python
# cli_bench.py additions
@app.command("compare")
def compare_cmd(
    a: str = typer.Argument(..., help="Run id, run dir or summary path."),
    b: str = typer.Argument(None, help="Second run; omit with --baseline."),
    baseline: str = typer.Option(None, "--baseline",
                                 help="'latest-release' or a summary."),
    force: bool = typer.Option(False, "--force"),
    all_rows: bool = typer.Option(False, "--all",
                                  help="Include noise and unchanged rows."),
) -> None:
    """Compare two runs, metric by metric."""
    from aegis.bench import BenchError
    from aegis.bench.compare import compare, latest_release, load_summary
    from aegis.bench.report import print_compare
    try:
        if baseline:
            base = (latest_release() if baseline == "latest-release"
                    else load_summary(baseline))
            if base is None:
                raise BenchError("no saved release in bench/history")
            left, right = base, load_summary(a)
        else:
            if b is None:
                raise typer.BadParameter("give two runs or --baseline")
            left, right = load_summary(a), load_summary(b)
        rows = compare(left, right, force=force)
    except BenchError as exc:
        _console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    print_compare(rows, _console, a_label=left["fingerprint"]["aegis_build"],
                  b_label=right["fingerprint"]["aegis_build"],
                  all_rows=all_rows)
    regressed = any(r.verdict == "regressed" for rs in rows.values()
                    for r in rs)
    raise typer.Exit(1 if regressed else 0)


_HISTORY_METRICS = ["latency.marker_ms.p50", "latency.marker_ms.p95",
                    "render.tick_ms.p95", "loop.lag_ms.p99",
                    "cpu.daemon_s_per_s", "mem.rss_peak_mb"]


@app.command("history")
def history_cmd(
    metric: list[str] = typer.Option(None, "--metric", "-m"),
    scenario: str = typer.Option("claude-blocks", "--scenario", "-s"),
) -> None:
    """Show metrics across saved releases on this host."""
    import json
    import re

    from aegis.bench.report import print_history
    from aegis.bench.runner import history_dir
    base = history_dir()
    def key(p):
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", p.stem)
        return (tuple(int(x) for x in m.groups()) if m else (0, 0, 0), p.stem)
    files = sorted(base.glob("*.json"), key=key) if base.exists() else []
    summaries = [json.loads(p.read_text()) for p in files]
    print_history(summaries, list(metric or _HISTORY_METRICS), scenario,
                  _console)
```

- [x] **Step 7: Run tests and a real compare**

Run: `uv run pytest tests/bench -q`
Expected: PASS (strict xfail still present).
Run two `claude-blocks` runs with `--repeat 3`, then `uv run aegis bench compare <run1> <run2> --all`.
Expected: nearly every timing row reads `noise`; if many read `regressed`/`improved` between identical code, the thresholds are too tight for this machine. Record what you saw in the plan and raise the floors in `METRICS`, not `REL`.

- [x] **Step 8: Commit**

```bash
git commit -m "feat(bench): compare, history, fake ACP agent, acp-stream and typing" -- src/aegis/bench tests/bench src/aegis/cli_bench.py
```

---

### Task 8: remaining scenarios

**Files:**
- Modify: `src/aegis/bench/scenarios.py`
- Test: `tests/bench/test_runner.py` (remove the strict xfail)

- [x] **Step 1: Add `idle`, `many-tabs`, `two-clients`, `claude-stream`, `soak`**

```python
def idle(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"ack": synthetic_blocks(3, 0, mark=False)}))
    ctx.send_prompt(rig, "ack")
    for n in (2, 3):
        rig.write(CTRL_T)
        ctx.pump_for(1.5)
        ctx.send_prompt(rig, "ack")
    ctx.wait_turns(3, timeout_s=60)
    ctx.wait_quiet(rig, 1000)
    with ctx.window():
        ctx.pump_for(20.0)


def many_tabs(ctx: ScenarioContext) -> None:
    script = make_script({"bg": acp_chunks(1500, 50, mark=False),
                          "go": acp_chunks(400, 50)})
    rig = ctx.boot(script, default_agent="bench-acp")
    ctx.send_prompt(rig, "bg")
    for _ in range(5):
        rig.write(CTRL_T)
        ctx.pump_for(1.5)
        ctx.send_prompt(rig, "bg")
    rig.write(CTRL_T)
    ctx.pump_for(1.5)
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        if not wait_until(ctx.rigs, lambda: any(
                r["k"] == "turn_end" and r["lines"] >= 400
                for r in ctx._emits("turn_end")), 120):
            raise BenchError("visible stream did not finish")
        ctx.pump_for(1.0)


def two_clients(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": synthetic_blocks(200, 50)}))
    ctx.expect_markers("a")
    ctx.expect_markers("b")
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.pump_for(2.0)
        ctx.attach("b")
        ctx.wait_turns(1, timeout_s=180)
        ctx.pump_for(1.0)


def claude_stream(ctx: ScenarioContext) -> None:
    try:
        steps = load_fixture("claude-stream")
    except FileNotFoundError as exc:
        raise ScenarioSkipped("fixture claude-stream not recorded") from exc
    rig = ctx.boot(make_script({"go": steps}))
    ctx.send_prompt(rig, "go")
    argv = ctx._emits("argv")
    if argv and not argv[-1]["partial"]:
        raise ScenarioSkipped(
            "aegis does not pass --include-partial-messages")
    ctx.expect_markers()
    with ctx.window():
        ctx.wait_turns(1, timeout_s=300)
        ctx.pump_for(1.0)


def soak(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({
        "soak": synthetic_fill(100) + synthetic_blocks(50, 5, mark=False)}))
    with ctx.window():
        deadline = time.monotonic() + 600
        turns = 0
        while time.monotonic() < deadline:
            ctx.send_prompt(rig, "soak")
            turns += 1
            ctx.wait_turns(turns, timeout_s=300)
    ends = ctx._emits("turn_end")
    lines = sum(r["lines"] for r in ends)
    samples = [r for r in read_jsonl(ctx.rep_dir / "probe.jsonl")
               if r["k"] == "sample" and r.get("rss")]
    if len(samples) >= 2 and lines:
        growth = (samples[-1]["rss"] - samples[0]["rss"]) / 2**20
        ctx.metric("mem.rss_growth_mb_per_1k_lines", growth / (lines / 1000))
```

`claude-stream` sends the prompt before opening the window, because the fake only starts (and writes its `argv` record) when aegis spawns it for the first turn. Its markers are emitted inside the window because the fixture's first delay is longer than the time to read `argv`; if a run reports lost markers for the first few, open the window before `send_prompt` and check `argv` right after.

`claude-blocks` switches to the fixture in Task 9. Register `idle`, `many-tabs` (`topologies` both), `two-clients` (`topologies=("daemon",)`), `claude-stream`, `soak` and set:

```python
DEFAULT = ["startup", "idle", "claude-blocks", "claude-stream", "acp-stream",
           "deep-stream", "resize", "typing", "many-tabs", "two-clients"]
QUICK = ["startup", "claude-blocks", "acp-stream", "resize"]
```

Import `load_fixture` from `aegis.bench.script`.

- [x] **Step 2: Remove the strict xfail and run everything**

Run: `uv run pytest tests/bench -q`
Expected: PASS.
Run: `uv run aegis bench run --repeat 1`
Expected: every scenario `ok` except `claude-stream` (`skipped`, fixture not recorded yet); exit 0. `soak` is not in the default set.

- [x] **Step 3: Confirm no leftovers**

Run: `ps -eo pid,args | grep -c "[a]egis-bench-"` → `0`; `ls -d /tmp/aegis-bench-* 2>/dev/null | wc -l` → `0`.

- [x] **Step 4: Commit**

```bash
git commit -m "feat(bench): idle, many-tabs, two-clients, claude-stream and soak scenarios" -- src/aegis/bench/scenarios.py tests/bench/test_runner.py
```

---

### Task 9: recorded fixtures

**Files:**
- Create: `src/aegis/bench/record.py`, `src/aegis/bench/fixtures/claude-blocks.jsonl`, `src/aegis/bench/fixtures/claude-stream.jsonl`
- Modify: `src/aegis/cli_bench.py` (add `record`), `src/aegis/bench/scenarios.py` (`claude-blocks` uses the fixture), `pyproject.toml` only if the wheel excludes `*.jsonl` (check `[tool.hatch.build.targets.wheel]`)
- Test: `tests/bench/test_record.py`

**Interfaces:**
- Produces: `record_fixture(out: Path, *, partial: bool, prompt: str = RECORD_PROMPT, model: str = "sonnet", claude: str = "claude") -> int` (lines written); `sanitize(line: dict, work: str) -> dict | None` (drops `system`/`result`, strips `uuid`, replaces the work dir with `/work`).

- [x] **Step 1: Failing test for `sanitize`**

```python
# tests/bench/test_record.py
from aegis.bench.record import sanitize


def test_sanitize_drops_owned_lines_and_scrubs_paths():
    assert sanitize({"type": "system", "subtype": "init"}, "/tmp/w") is None
    assert sanitize({"type": "result"}, "/tmp/w") is None
    line = {"type": "user", "uuid": "u", "session_id": "s", "message": {
        "content": [{"type": "tool_result", "content": "/tmp/w/notes.md ok"}]}}
    out = sanitize(line, "/tmp/w")
    assert "uuid" not in out and "session_id" not in out
    assert out["message"]["content"][0]["content"] == "/work/notes.md ok"
```

- [x] **Step 2: Implement**

```python
# src/aegis/bench/record.py
"""Record a real claude session as a replayable fixture.

Costs real tokens (a few cents with sonnet). The fixture keeps each
line's delay after the previous one, so replay reproduces the real
arrival pattern: bursts of tool calls, a long pause, then text.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

RECORD_PROMPT = (
    "Read notes.md in this directory. Then write a detailed markdown "
    "explanation, about 700 words, of how a terminal user interface decides "
    "what to redraw on each frame. Use headings, a bulleted list, a table "
    "and one python code block.")

_NOTES = ("Terminal UIs keep a model of the screen and diff it against the "
          "previous frame. Synchronized output (DEC mode 2026) lets the "
          "terminal present a frame atomically.\n")


def sanitize(line: dict, work: str) -> dict | None:
    if line.get("type") in ("system", "result"):
        return None
    line = {k: v for k, v in line.items() if k not in ("uuid", "session_id")}
    return json.loads(json.dumps(line).replace(work, "/work"))


def record_fixture(out: Path, *, partial: bool, prompt: str = RECORD_PROMPT,
                   model: str = "sonnet", claude: str = "claude") -> int:
    work = tempfile.mkdtemp(prefix="aegis-bench-record-")
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
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps({"type": "user", "message": {
        "role": "user", "content": prompt}}) + "\n")
    proc.stdin.flush()
    steps, last = [], time.monotonic()
    for raw in proc.stdout:
        now = time.monotonic()
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        dt_ms = (now - last) * 1000
        last = now
        if obj.get("type") == "result":
            break
        clean = sanitize(obj, work)
        if clean is not None:
            steps.append({"dt_ms": round(min(dt_ms, 5000.0), 1),
                          "mark": True, "line": clean})
    proc.stdin.close()
    proc.wait(timeout=30)
    if steps:
        steps[0]["dt_ms"] = min(steps[0]["dt_ms"], 1000.0)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n"
                           for s in steps))
    return len(steps)
```

Add to `cli_bench.py`:

```python
@app.command("record")
def record_cmd(
    partial: bool = typer.Option(False, "--partial",
                                 help="Record with --include-partial-messages."),
    prompt: str = typer.Option(None, "--prompt"),
    out: Path = typer.Option(None, "--out"),
    model: str = typer.Option("sonnet", "--model"),
) -> None:
    """Record a real claude session as a fixture (costs tokens)."""
    from aegis.bench.record import RECORD_PROMPT, record_fixture
    name = "claude-stream" if partial else "claude-blocks"
    dest = out or Path(__file__).parent / "bench" / "fixtures" / f"{name}.jsonl"
    n = record_fixture(dest, partial=partial, prompt=prompt or RECORD_PROMPT,
                       model=model)
    _console.print(f"recorded {n} lines -> {dest}")
```

- [x] **Step 3: Record both fixtures**

Run (network, real tokens): `uv run aegis bench record` and `uv run aegis bench record --partial`
Expected: dozens of lines each; `claude-stream.jsonl` contains `stream_event` lines; `grep -c /home/apiad src/aegis/bench/fixtures/*.jsonl` prints `0` for both (nothing personal leaked; if not 0, extend `sanitize` and re-record).

- [x] **Step 4: Switch `claude-blocks` to the fixture**

```python
def claude_blocks(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": load_fixture("claude-blocks")}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=300)
        ctx.pump_for(1.0)
```

Check the wheel includes the fixtures: `uv build --wheel -o /tmp/aegis-wheel && unzip -l /tmp/aegis-wheel/*.whl | grep fixtures` lists both files. Remove `/tmp/aegis-wheel` afterwards.

- [x] **Step 5: Run**

Run: `uv run pytest tests/bench -q` then `uv run aegis bench run -s claude-blocks -s claude-stream --repeat 1`
Expected: `claude-blocks` ok with markers, `claude-stream` `skipped: aegis does not pass --include-partial-messages`.

- [x] **Step 6: Commit**

```bash
git commit -m "feat(bench): record real claude sessions as replay fixtures" -- src/aegis/bench/record.py src/aegis/bench/fixtures src/aegis/bench/scenarios.py src/aegis/cli_bench.py tests/bench/test_record.py
```

---

### Task 10: selftest, targets and profiling

**Files:**
- Modify: `src/aegis/cli_bench.py` (add `selftest`), `src/aegis/bench/scenarios.py` (hidden `selftest-stream`)
- Test: manual verification steps (these are the checks that the measurement itself works)

- [x] **Step 1: Hidden selftest scenario**

```python
def selftest_stream(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": synthetic_blocks(60, 100)}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=120)
        ctx.pump_for(1.0)
```

Register it in `SCENARIOS` but not in `DEFAULT` or `QUICK`; `aegis bench list` shows it with its description "used by selftest".

- [x] **Step 2: `selftest` command**

```python
@app.command("selftest")
def selftest_cmd(sabotage: int = typer.Option(40, "--sabotage")) -> None:
    """Prove the bench sees an injected regression."""
    from aegis.bench.runner import RunOptions, failed, run
    base_opts = dict(scenarios=["selftest-stream"], repeat=1)
    plain, _ = run(RunOptions(**base_opts), _console)
    slow, _ = run(RunOptions(**base_opts, sabotage_ms=sabotage), _console)
    if failed(plain) or failed(slow):
        _console.print("[red]selftest: a run failed its gates[/]")
        raise typer.Exit(1)
    a = plain["scenarios"]["selftest-stream"]["median"]
    b = slow["scenarios"]["selftest-stream"]["median"]
    lat = b["latency.marker_ms.p50"] - a["latency.marker_ms.p50"]
    paint = b["render.paint_ms.p50"] - a["render.paint_ms.p50"]
    ok = lat >= 0.75 * sabotage and paint >= 0.9 * sabotage
    _console.print(f"marker p50 +{lat:.1f} ms (need >= {0.75 * sabotage:.0f}), "
                   f"paint p50 +{paint:.1f} ms (need >= {0.9 * sabotage:.0f}): "
                   + ("[green]pass[/]" if ok else "[red]fail[/]"))
    raise typer.Exit(0 if ok else 1)
```

Run: `uv run aegis bench selftest`, read the rc as its own command.
Expected: pass. Then prove the selftest can fail: `uv run aegis bench selftest --sabotage 0` must exit 1.

- [x] **Step 3: Older release target**

Run: `uv run aegis bench run --target 0.37.0 -s claude-blocks -s startup -s two-clients --repeat 1`
Expected: topology `in-process`; `two-clients` skipped; `claude-blocks` ok; `probe_sync` gate true. If the in-process client never negotiates sync or the probe's optional pane hook is missing, the run still passes (optional); a missing *required* hook is a real failure to investigate, not to relax.

- [x] **Step 4: Profiling**

Run: `uv run aegis bench run -s claude-blocks --repeat 1 --profile`
Expected: `profile.speedscope.json` in the repeat dir, non-empty. If `py-spy` cannot attach (it launches the daemon as its own child, which `ptrace_scope=1` allows), write the failure into `serve.log` expectations in `know-how/benchmarking.md` and make `--profile` exit 2 with that message rather than a silent empty file.

- [x] **Step 5: Commit**

```bash
git commit -m "feat(bench): selftest proves the rig sees a regression" -- src/aegis/cli_bench.py src/aegis/bench/scenarios.py
```

---

### Task 11: docs, release step, first history

**Files:**
- Create: `know-how/benchmarking.md`, `bench/history/zion/<version>-<sha>.json`, `bench/history/zion/0.37.0.json`
- Modify: `AGENTS.md` (know-how index + layout entry), `know-how/releasing.md` (bench step), `CHANGELOG.md` (`[Unreleased]` → `### Added`), `docs/superpowers/specs/2026-09-13-aegis-bench-design.md` (status), this plan (check boxes)

- [x] **Step 1: `know-how/benchmarking.md`** covering: when to reach for it; `aegis bench run --quick` vs full; reading a summary (window, medians, gates); `compare --baseline latest-release`; `--target` topologies and what an in-process vs daemon comparison means; `selftest` before trusting a surprising result; traps: never run against a loaded box (record `governor`), a dirty tree is marked `-dirty`, `--keep` worlds live in `/tmp`, counts only cover `Widget` base methods, `claude-stream` skips until aegis requests partial messages, the probe measures up to bytes on the pty and not the terminal emulator.
- [x] **Step 2: AGENTS.md** know-how bullet: `know-how/benchmarking.md` — *reach for it when measuring TUI rendering, latency, CPU or memory, comparing releases, or before claiming a performance change.* Layout bullet for `src/aegis/bench/` and `src/aegis/cli_bench.py`.
- [x] **Step 3: releasing.md**: before tagging, run `uv run aegis bench run --save` on zion (idle machine), `uv run aegis bench compare --baseline latest-release <run-id>`, and commit `bench/history/zion/<version>.json` with the release. For the release file, rename the saved `<version>-<sha>.json` to `<version>.json` once the tag's commit is HEAD.
- [x] **Step 4: CHANGELOG** `[Unreleased]` / `### Added`: `aegis bench` with one line on what it measures and the history file.
- [x] **Step 5: Save the first history**

Run: `uv run aegis bench run --save` (full default set, 3 repeats) and `uv run aegis bench run --target 0.37.0 --save`
Then: `uv run aegis bench history -s claude-blocks` shows both rows.

- [x] **Step 6: Gates**

Run: `uv run pytest tests/bench -q`, `uv run ruff check src/aegis/bench src/aegis/cli_bench.py`, `uv run ty check src/aegis/bench`, `rift check` (each rc read on its own).
Run the blast-radius subset of the existing suite: `uv run pytest tests/cli tests/daemon -q` (the full suite has known inotify flakes).

- [x] **Step 7: Flip statuses and commit**

Spec status → `Implemented 2026-09-13`. Check off this plan's boxes.

```bash
git commit -m "docs(bench): know-how, release step, changelog and first history" -- know-how/benchmarking.md AGENTS.md know-how/releasing.md CHANGELOG.md bench/history docs/superpowers/specs/2026-09-13-aegis-bench-design.md docs/superpowers/plans/2026-09-13-aegis-bench.md
git push origin main
```
