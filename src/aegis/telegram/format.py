from __future__ import annotations

from typing import NamedTuple

from aegis.telegram.format_html import render as render_html


class Spillover(NamedTuple):
    raw_md: str
    rendered_html: str


def status_line(handle: str, state: str, model: str, metrics: str) -> str:
    icon = {"working": "⏳", "ready": "✅", "error": "⚠️"}.get(state, "•")
    return f"{icon} {handle} · {state} · {model} {metrics}"


def chunk(html: str, raw_md: str, *,
          max_parts: int = 3,
          limit: int = 4096) -> list[str] | Spillover:
    raise NotImplementedError  # Task 6


__all__ = ["Spillover", "status_line", "chunk", "render_html"]
