from __future__ import annotations

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown, Event,
)


# Glyph per semantic kind (parity with ACP's tool_call kind enum;
# claude paths derive kind from the tool name in events.py).
_KIND_ICON = {
    "read": "📖",
    "edit": "✏️",
    "execute": "⌬",
    "search": "🔎",
    "think": "✻",
    "fetch": "🌐",
    "move": "➡️",
    "delete": "🗑",
    "switch_mode": "🔄",
    "other": "⏺",
}


def _pathhint(ev: ToolUse) -> str:
    """One-line context for a tool call: the tail of the first known
    location (with :line suffix when known), falling back to the tool's
    legacy summary string."""
    if ev.locations:
        path, line = ev.locations[0]
        tail = path.rsplit("/", 1)[-1] if path else ""
        if line is not None:
            return f"{tail}:{line}"
        return tail
    return ev.summary


def render_event(ev: Event, colors) -> RenderableType | None:
    """Map one typed event to a Rich renderable (themed), or None."""
    if isinstance(ev, AssistantText):
        text = ev.text.strip()
        return Markdown(text) if text else None
    if isinstance(ev, AssistantThinking):
        body = (ev.text or "").strip()
        if not body:
            return Text("✻ Thinking…", style=colors.muted)
        return Text(f"✻ {body}", style=f"italic {colors.muted}")
    if isinstance(ev, ToolUse):
        icon = _KIND_ICON.get(ev.kind or "", "⏺")
        hint = _pathhint(ev)
        arg = f"({hint})" if hint else ""
        return Text.assemble((f"{icon} ", colors.accent), f"{ev.name}{arg}")
    if isinstance(ev, ToolResult):
        first = ev.text.splitlines()[0] if ev.text.strip() else ""
        if len(first) > 100:
            first = first[:100] + "…"
        if ev.is_error:
            return Text.assemble(("  └ ", colors.muted),
                                 ("error ", colors.err), first)
        return Text.assemble(("  └ ", colors.muted),
                             ("ok ", colors.ok), first)
    if isinstance(ev, Result):
        secs = (ev.duration_ms or 0) / 1000
        return Text(f"── done in {secs:.1f}s ──", style=colors.muted)
    if isinstance(ev, (SystemInit, Unknown)):
        return None
    return None


def render_user_line(text: str, colors, width: int | None = None) -> Text:
    """The user's message line: accent `›` prefix on a lighter band.

    The whole line carries `colors.user_bg`; padded to `width` (when known)
    so the tint reads as a full-width band, not just behind the glyphs.
    """
    line = Text(style=f"{colors.user} on {colors.user_bg}")
    line.append("› ", style=f"bold {colors.user} on {colors.user_bg}")
    line.append(text, style=f"{colors.user} on {colors.user_bg}")
    if width and width > line.cell_len:
        line.pad_right(width - line.cell_len)
    return line


def render_inbox_block(msg, colors, *, preview_lines: int = 4) -> Text:
    """Visible block for an incoming inbox message.

    Header line carries the sender / task / status / timestamp; below it
    we show up to `preview_lines` body lines (dimmed) so Alex can see
    what the agent is about to react to without scrolling into the next
    turn. Truncation footer says how many more lines were elided.
    """
    line = Text()
    line.append("✉ ", style=f"bold {colors.accent}")
    if msg.task_id is not None:
        status = msg.status or "?"
        head = (f"from {msg.sender} · task#{msg.task_id} · "
                f"{status} · {msg.timestamp}")
    else:
        head = f"from {msg.sender} · {msg.timestamp}"
    line.append(head, style=colors.accent)
    line.append("\n")
    body_lines = msg.body.splitlines() if msg.body else []
    for ln in body_lines[:preview_lines]:
        line.append(f"  {ln}\n", style=colors.muted)
    if len(body_lines) > preview_lines:
        remaining = len(body_lines) - preview_lines
        s = "" if remaining == 1 else "s"
        line.append(f"  … ({remaining} more line{s})\n",
                    style=colors.muted)
    return line
