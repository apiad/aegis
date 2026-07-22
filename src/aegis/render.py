from __future__ import annotations

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text

from dataclasses import replace

from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, PlanEntry, ToolUse,
    ToolResult, Result, SystemInit, Unknown, Event,
)
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, describe_tool, diff_window,
    format_tool_args, result_parts,
)

# Per-tool-call spinner (mirrors the turn-level WorkingIndicator glyphs).
_TOOL_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _fmt_dur(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s"


def render_tool_use(ev, colors, *, elapsed: float | None = None,
                    running: bool = False, frame: int = 0,
                    expanded: bool = False) -> RenderableType:
    """One tool-call line: kind icon + human description, with an optional
    per-tool spinner+timer (while running), a frozen duration (once done, if
    ≥1s), and the full args block (when expanded). The args stay collapsed by
    default — the pane expands them on click."""
    icon = KIND_ICON.get(ev.kind or "", "⏺")
    desc = describe_tool(ev.name, ev.raw_input, ev.summary, ev.locations)
    line = Text.assemble((f"{icon} ", colors.accent), desc)
    if running and elapsed is not None:
        spin = _TOOL_SPINNER[frame % len(_TOOL_SPINNER)]
        line.append(f"  {spin} {_fmt_dur(elapsed)}", style=colors.muted)
    elif not running and elapsed is not None and elapsed >= 1.0:
        line.append(f"  · {_fmt_dur(elapsed)}", style=colors.muted)
    if expanded:
        args = format_tool_args(ev.name, ev.raw_input, ev.summary)
        if args:
            body = Text()
            for ln in args.splitlines():
                body.append(f"    {ln}\n", style=colors.muted)
            return Group(line, body)
    return line


def _render_diff(diff: tuple[str, str, str], colors,
                  max_lines: int = 6) -> "Text":
    """Render a (path, old_text, new_text) tuple as a small unified
    preview using the shared diff windowing — at most `max_lines` total
    visible removed+added rows, with a "… N more" footer when truncated.
    """
    path, old_text, new_text = diff
    removed, added, elided = diff_window(old_text, new_text, max_lines)

    body = Text()
    body.append(f"  ┌ {path}\n", style=colors.muted)
    for line in removed:
        body.append(f"  │ -", style=colors.err)
        body.append(f" {line}\n", style=colors.err)
    for line in added:
        body.append(f"  │ +", style=colors.ok)
        body.append(f" {line}\n", style=colors.ok)
    if elided > 0:
        body.append(f"  │ … {elided} more line"
                    f"{'s' if elided != 1 else ''}\n",
                    style=colors.muted)
    body.append("  └", style=colors.muted)
    return body


def _render_agent_plan(plan: AgentPlan, colors) -> "RenderableType":
    """Render an AgentPlan as a fenced status block with one row per
    entry. Header summarizes progress (e.g. "2/4 done"). High-priority
    entries get bolded content; low-priority entries dim. Empty plans
    render a single muted "(no plan)" line so the model's
    "I'm clearing my plan" signal is still visible."""
    total = len(plan.entries)
    if total == 0:
        return Text("📋 (no plan)", style=colors.muted)
    done = sum(1 for e in plan.entries if e.status == "completed")
    body = Text()
    body.append(f"📋 Plan — {done}/{total} done\n",
                style=f"bold {colors.accent}")
    for entry in plan.entries:
        glyph = PLAN_STATUS_GLYPH.get(entry.status, "○")
        glyph_style = (
            colors.ok if entry.status == "completed"
            else colors.accent if entry.status == "in_progress"
            else colors.muted
        )
        content_style = ""
        if entry.priority == "high":
            content_style = "bold"
        elif entry.priority == "low":
            content_style = colors.muted
        body.append(f"  {glyph} ", style=glyph_style)
        body.append(entry.content + "\n", style=content_style)
    return body


def coalesce_chunks(events: list[Event]) -> list[Event]:
    """Merge adjacent AssistantText / AssistantThinking events that
    share the same (kind, message_id) into a single concatenated
    event. Any non-chunk event breaks the run. When message_id is
    None on both sides, falls back to grouping by kind alone (the
    pre-slice-2 claude case).

    Used by the replay path so a persisted token-stream renders as
    one block per assistant message, not 116 separate lines. The
    last chunk's usage carries the running total — preserve it on
    the merged event.

    Pure function; no rendering, no I/O."""
    if not events:
        return []
    out: list[Event] = []
    buf: AssistantText | AssistantThinking | None = None
    for ev in events:
        if isinstance(ev, (AssistantText, AssistantThinking)):
            if buf is not None and type(buf) is type(ev) \
                    and buf.message_id == ev.message_id:
                buf = replace(buf, text=buf.text + ev.text,
                              usage=ev.usage or buf.usage)
                continue
            if buf is not None:
                out.append(buf)
            buf = ev
        else:
            if buf is not None:
                out.append(buf)
                buf = None
            out.append(ev)
    if buf is not None:
        out.append(buf)
    return out


def render_event(ev: Event, colors) -> RenderableType | None:
    """Map one typed event to a Rich renderable (themed), or None."""
    if isinstance(ev, AssistantText):
        text = ev.text.strip()
        return Markdown(text) if text else None
    if isinstance(ev, AssistantThinking):
        # Compact 'thought' summary (matches the live pane). Replay has no
        # recorded duration, so only the token count is shown; the full
        # reasoning stays in the copy payload. Prefer the harness-reported
        # estimate (Claude redacts thinking text → len is 0); fall back to a
        # ~4-chars/token heuristic for harnesses that stream the text instead.
        from aegis.tui.metrics import _fmt_tokens
        approx = (ev.token_estimate if ev.token_estimate > 0
                  else max(1, len((ev.text or "").strip()) // 4))
        return Text(f"💭 thought · ~{_fmt_tokens(approx)} tok",
                    style=f"italic {colors.muted}")
    if isinstance(ev, ToolUse):
        # Static path (replay / non-live). The live pane re-renders through
        # render_tool_use with a per-tool timer + click-to-expand args.
        return render_tool_use(ev, colors)
    if isinstance(ev, ToolResult):
        if ev.diff is not None and not ev.is_error:
            return _render_diff(ev.diff, colors)
        first = ev.text.splitlines()[0] if ev.text.strip() else ""
        if len(first) > 100:
            first = first[:100] + "…"
        if ev.is_error:
            return Text.assemble(("  └ ", colors.muted),
                                 ("error ", colors.err), first)
        return Text.assemble(("  └ ", colors.muted),
                             ("ok ", colors.ok), first)
    if isinstance(ev, AgentPlan):
        return _render_agent_plan(ev, colors)
    if isinstance(ev, Result):
        return Text(f"── {' · '.join(result_parts(ev))} ──",
                    style=colors.muted)
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


def render_command_block(result, colors, width: int | None = None) -> Text:
    """Visible block for a slash-command result (`/help`, `/spawn`, …).

    A `/`-glyph header in the accent colour with the result title, then the
    body dimmed beneath. The whole block tints `colors.error` when the
    command failed (unknown command, bad args, handler exception).
    """
    tint = colors.error if not result.ok else colors.accent
    line = Text()
    line.append("/ ", style=f"bold {tint}")
    line.append(result.title, style=tint)
    if result.body:
        line.append("\n")
        for ln in result.body.splitlines():
            line.append(f"  {ln}\n", style=colors.muted)
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
