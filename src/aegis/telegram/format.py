from __future__ import annotations

from typing import NamedTuple

from aegis.telegram.format_html import render as render_html


class Spillover(NamedTuple):
    raw_md: str
    rendered_html: str


def status_line(handle: str, state: str, model: str, metrics: str) -> str:
    icon = {"working": "⏳", "ready": "✅", "error": "⚠️"}.get(state, "•")
    return f"{icon} {handle} · {state} · {model} {metrics}"


def _split_blocks(html: str) -> list[str]:
    """Split HTML into atomic blocks on \\n\\n, never inside <pre>...</pre>."""
    blocks: list[str] = []
    i = 0
    n = len(html)
    while i < n:
        if html.startswith("<pre>", i):
            end = html.find("</pre>", i)
            if end == -1:
                blocks.append(html[i:])
                break
            end += len("</pre>")
            blocks.append(html[i:end])
            i = end
            while i < n and html[i] == "\n":
                i += 1
        else:
            j = i
            while j < n:
                if html.startswith("<pre>", j):
                    break
                if html.startswith("\n\n", j):
                    break
                j += 1
            blocks.append(html[i:j])
            i = j
            while i < n and html[i] == "\n":
                i += 1
    return [b for b in blocks if b]


def _greedy_pack(blocks: list[str], *, limit: int) -> list[str]:
    parts: list[str] = []
    cur = ""
    for b in blocks:
        candidate = b if not cur else cur + "\n\n" + b
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                parts.append(cur)
            cur = b
    if cur:
        parts.append(cur)
    return parts


def chunk(html: str, raw_md: str, *,
          max_parts: int = 3,
          limit: int = 4096) -> list[str] | Spillover:
    blocks = _split_blocks(html)
    for b in blocks:
        if len(b) > limit and b.startswith("<pre>"):
            return Spillover(raw_md=raw_md, rendered_html=html)
    parts = _greedy_pack(blocks, limit=limit)
    if len(parts) > max_parts:
        return Spillover(raw_md=raw_md, rendered_html=html)
    return parts


__all__ = ["Spillover", "status_line", "chunk", "render_html"]
