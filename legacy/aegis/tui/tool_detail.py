"""The window behind a tool call.

The transcript gives a call exactly one row (``render.render_tool_use``).
Everything that row cannot hold — the whole command, the whole diff, the
whole output — is here, one click away.

One scroll region, not two panes: the input is a few lines you read before
you start scrolling, a pinned pane would cost those rows on every call, and
two panes raise a question — which one does PgDn move? — that one region
never asks. The command stays legible while you read a failure because it
is also in the title bar.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from aegis.render import _fmt_dur, _render_diff
from aegis.render_shared import describe_tool, format_tool_args, tool_glyph

# Textual lays out every mounted row, so a 50,000-line result would cost a
# full-screen reflow to show a screenful. The pane holds the whole string
# either way — `c` copies all of it and `f` writes it out and opens it in a
# file tab, which is a real viewer with a search box.
OUTPUT_MAX_ROWS = 2000


def _section(title: str, colors, note: str = "") -> Text:
    line = Text()
    line.append(f"{title}", style=f"bold {colors.accent}")
    if note:
        line.append(f"   {note}", style=colors.muted)
    line.append("\n")
    return line


def detail_body(use, result, colors, *, max_rows: int = OUTPUT_MAX_ROWS):
    """The scrollable contents: the call's full input, then its full output.

    Returns ``(renderable, dropped_row_count)``. Pure — no widgets, so the
    interesting parts are testable without a running app.
    """
    parts: list[RenderableType] = [_section("input", colors)]
    if use.name in ("Edit", "Write") and result is not None and result.diff:
        # The diff IS the call; an old_string/new_string dump is a worse
        # rendering of the same thing. No line budget here.
        parts.append(_render_diff(result.diff, colors, max_lines=10**9))
        parts.append(Text("\n"))
    else:
        args = format_tool_args(use.name, use.raw_input, use.summary, cap=None)
        parts.append(Text(f"{args or '(no arguments)'}\n", style=colors.muted))

    if result is None:
        parts.append(Text("\nstill running…", style=colors.working))
        return Group(*parts), 0

    lines = (result.text or "").splitlines()
    dropped = max(0, len(lines) - max_rows)
    note = f"{len(lines):,} lines" if lines else "no output"
    parts.append(Text("\n"))
    parts.append(_section("output", colors, note))
    parts.append(
        Text("\n".join(lines[:max_rows]), style=colors.err if result.is_error else "")
    )
    if dropped:
        parts.append(
            Text(
                f"\n… {dropped:,} more lines — c copies all of it, f opens the whole thing",
                style=colors.muted,
            )
        )
    return Group(*parts), dropped


class ToolDetailScreen(ModalScreen):
    """The full input and output of one tool call."""

    DEFAULT_CSS = """
    ToolDetailScreen { align: center middle; }
    ToolDetailScreen #td-box {
        width: 90%; height: 85%;
        border: round $panel; background: $surface; padding: 0 2;
    }
    ToolDetailScreen #td-title { width: 100%; height: 1; }
    ToolDetailScreen #td-scroll { width: 100%; height: 1fr; }
    ToolDetailScreen #td-body { width: 100%; height: auto; }
    ToolDetailScreen #td-help { dock: bottom; width: 100%; height: 1;
                                color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("c", "copy", "Copy output", show=False),
        Binding("f", "open_as_file", "Open as file", show=False),
        Binding("n", "step(1)", "Next call", show=False),
        Binding("p", "step(-1)", "Previous call", show=False),
    ]

    def __init__(self, use, result, elapsed, colors, *, on_step=None) -> None:
        super().__init__()
        self._use, self._result, self._elapsed = use, result, elapsed
        self._colors = colors
        # Called with +1/-1 to swap this screen's contents for the next or
        # previous call. Scanning a turn one open-and-close at a time is
        # what would stop anyone using this window.
        self._on_step = on_step

    def compose(self) -> ComposeResult:
        with Vertical(id="td-box"):
            yield Label(self._title(), id="td-title")
            with VerticalScroll(id="td-scroll"):
                yield Static(
                    detail_body(self._use, self._result, self._colors)[0], id="td-body"
                )
            yield Label(
                "↑↓ PgDn scroll · c copy · f open as file · n/p next call · esc close",
                id="td-help",
            )

    def on_mount(self) -> None:
        self.query_one("#td-scroll", VerticalScroll).focus()

    def _title(self) -> Text:
        glyph = tool_glyph(self._use.name, self._use.kind, self._use.raw_input)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f"{glyph} ", style=self._colors.accent)
        t.append(
            describe_tool(
                self._use.name,
                self._use.raw_input,
                self._use.summary,
                self._use.locations,
            )
        )
        if self._result is not None:
            t.append("  ")
            t.append(
                "✗" if self._result.is_error else "✓",
                style=self._colors.err if self._result.is_error else self._colors.ok,
            )
        if self._elapsed is not None:
            t.append(f"  {_fmt_dur(self._elapsed)}", style=self._colors.muted)
        return t

    def show(self, use, result, elapsed) -> None:
        """Swap in another call without closing — what n/p drive."""
        self._use, self._result, self._elapsed = use, result, elapsed
        self.query_one("#td-title", Label).update(self._title())
        self.query_one("#td-body", Static).update(
            detail_body(use, result, self._colors)[0]
        )
        self.query_one("#td-scroll", VerticalScroll).scroll_home(animate=False)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_step(self, delta: int) -> None:
        if self._on_step is not None:
            self._on_step(delta)

    def action_copy(self) -> None:
        text = (self._result.text if self._result else "") or ""
        self.app.copy_to_clipboard(text)
        self.app.notify(f"copied {len(text)} chars", timeout=1.5)

    async def action_open_as_file(self) -> None:
        """Hand the whole output to a file tab.

        The 2,000-row cap has to have an exit, and `_open_file_tab` is
        already a scrollable viewer with a search box — better than growing
        one here."""
        import tempfile
        from pathlib import Path

        opener = getattr(self.app, "_open_file_tab", None)
        if opener is None or self._result is None:
            return
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix=f"{self._use.name}-", delete=False
        )
        with fd:
            fd.write(self._result.text or "")
        self.dismiss(None)
        await opener(Path(fd.name))
