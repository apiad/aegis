from __future__ import annotations

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown, Event,
)


def render_event(ev: Event, colors) -> RenderableType | None:
    """Map one typed event to a Rich renderable (themed), or None."""
    if isinstance(ev, AssistantText):
        text = ev.text.strip()
        return Markdown(text) if text else None
    if isinstance(ev, AssistantThinking):
        return Text("✻ Thinking…", style=colors.muted)
    if isinstance(ev, ToolUse):
        arg = f"({ev.summary})" if ev.summary else ""
        return Text.assemble(("⏺ ", colors.accent), f"{ev.name}{arg}")
    if isinstance(ev, ToolResult):
        first = ev.text.splitlines()[0] if ev.text.strip() else ""
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
