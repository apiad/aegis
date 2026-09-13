"""Workload scripts the fake agents replay.

A script maps the first word of a prompt to a list of steps, so one fake
binary serves every tab of a world: ``fill`` mounts history without
markers, ``bg`` streams in a background tab without markers, ``go`` is the
measured stream. Markers are only minted for ``mark: True`` steps, so a
hidden tab never produces a marker the rig could not possibly see.

Claude steps carry a stream-json ``line``; ACP steps carry a text
``chunk``. Both carry ``dt_ms``, the delay before the step is sent.
"""
from __future__ import annotations

import json
from importlib import resources

_LOREM = ("the compositor diffs spans against the previous frame and writes "
          "only what changed while the layout pass measures every "
          "widget").split()


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
    """History without delay: a text block, then a Read call and its result.

    Consecutive assistant texts coalesce into one transcript block, so the
    tool call between them is what makes each cycle mount separately.
    """
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
    """A recorded session from ``aegis/bench/fixtures``; raises
    ``FileNotFoundError`` when it has not been recorded."""
    raw = resources.files("aegis.bench").joinpath(
        "fixtures", f"{name}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def make_script(prompts: dict[str, list[dict]], *, speed: float = 1.0,
                default: list[dict] | None = None) -> dict:
    return {"speed": speed, "prompts": prompts, "default": default}


def route(script: dict, prompt_text: str) -> list[dict]:
    word = (prompt_text.strip().split() or [""])[0].lower()
    steps = (script.get("prompts") or {}).get(word)
    if steps is None:
        steps = script.get("default")
    if steps is None:
        steps = synthetic_blocks(1, 0, mark=False)
    return steps


def inject_marker(line: dict, m: str) -> bool:
    """Put marker ``m`` where the TUI will draw it; False if nothing drawable.

    Text is prefixed (the start of a new block is on screen when it
    mounts); a stream delta is suffixed (a delta extends the tail).
    """
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
