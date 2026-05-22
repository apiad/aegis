"""Render a Q/A dialogue into a spec-drafting prompt."""
from __future__ import annotations

from datetime import date


def render_spec_prompt(topic: str, answers: dict[str, str]) -> str:
    pairs = "\n".join(
        f"### Q: {q}\nA: {a}" for q, a in answers.items())
    return (
        f"You are drafting a design spec. Topic: {topic}\n\n"
        f"Below are the answers the user gave to clarifying questions. "
        f"Synthesize them into a complete design spec following the "
        f"conventions in docs/superpowers/specs/. Output the spec body "
        f"in markdown.\n\n{pairs}"
    )


def slugify(text: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch == "-" else "-"
        for ch in text.lower().replace(" ", "-"))
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:60]


def today_iso() -> str:
    return date.today().isoformat()
