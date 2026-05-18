from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown

from aegis.events import (
    AssistantText, AssistantThinking, ToolUse, ToolResult,
    Result, SystemInit, Unknown, Event,
)


class Renderer:
    def __init__(self, console: Console) -> None:
        self._c = console

    def render(self, ev: Event) -> None:
        if isinstance(ev, AssistantText):
            if ev.text.strip():
                self._c.print(Markdown(ev.text))
        elif isinstance(ev, AssistantThinking):
            self._c.print("[dim]✻ Thinking…[/dim]")
        elif isinstance(ev, ToolUse):
            arg = f"({ev.summary})" if ev.summary else ""
            self._c.print(f"[cyan]⏺[/cyan] {ev.name}{arg}")
        elif isinstance(ev, ToolResult):
            first = ev.text.splitlines()[0] if ev.text.strip() else ""
            mark = "[red]error[/red]" if ev.is_error else "[green]ok[/green]"
            self._c.print(f"  [dim]└[/dim] {mark} {first}")
        elif isinstance(ev, Result):
            secs = (ev.duration_ms or 0) / 1000
            self._c.print(f"[dim]── done in {secs:.1f}s ──[/dim]")
        elif isinstance(ev, (SystemInit, Unknown)):
            pass  # not part of the rendered view
