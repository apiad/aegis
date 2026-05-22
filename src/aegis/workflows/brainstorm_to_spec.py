"""brainstorm_to_spec — interactive Q/A with the user → spec doc.

Walks the user through five clarifying questions (or resumes from the
last checkpoint), then spawns a ``spec_writer`` subagent to synthesise
the answers into a markdown spec under ``docs/superpowers/specs/``.
"""
from __future__ import annotations

from pathlib import Path

from aegis.workflow import workflow
from aegis.workflows._lib.spec_renderer import (
    render_spec_prompt, slugify, today_iso,
)

_QUESTIONS = [
    "What's the problem this solves?",
    "Who is this for?",
    "What's the smallest version that's useful?",
    "What approaches have you considered?",
    "What's out of scope?",
]


@workflow("brainstorm_to_spec")
async def brainstorm_to_spec(engine, *, topic: str | None = None) -> str:
    state = await engine.resume_state() or {
        "phase": "topic", "answers": {}, "idx": 0}

    if state["phase"] == "topic":
        topic = topic or await engine.ask_human(
            "What are we brainstorming about?")
        state = {"phase": "questions", "topic": topic,
                 "answers": {}, "idx": 0}
        await engine.checkpoint("topic_set", state)

    while state["idx"] < len(_QUESTIONS):
        q = _QUESTIONS[state["idx"]]
        ans = await engine.ask_human(q)
        state["answers"][q] = ans
        state["idx"] += 1
        await engine.checkpoint(f"q_{state['idx']}", state)

    if state.get("spec_path") is None:
        writer = await engine.spawn("spec_writer")
        try:
            spec_text = await engine.send(writer, render_spec_prompt(
                topic=state["topic"], answers=state["answers"]))
        finally:
            await engine.close(writer)
        slug = slugify(state["topic"])
        path = f"docs/superpowers/specs/{today_iso()}-{slug}-design.md"
        out = _resolve(engine, path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(spec_text, encoding="utf-8")
        state["spec_path"] = path
        await engine.checkpoint("spec_written", state)

    engine.log(f"Spec written to {state['spec_path']}")
    return state["spec_path"]


def _resolve(engine, rel_path: str) -> Path:
    base = engine.config.get("cwd") if engine.config else None
    if base is None:
        from aegis.config import find_project_root
        base = str(find_project_root() or Path.cwd())
    return Path(base) / rel_path
