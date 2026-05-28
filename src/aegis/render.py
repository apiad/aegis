from __future__ import annotations

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from dataclasses import replace

from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, PlanEntry, ToolUse,
    ToolResult, Result, SystemInit, Unknown, Event,
)


def _render_diff(diff: tuple[str, str, str], colors,
                  max_lines: int = 6) -> "Text":
    """Render a (path, old_text, new_text) tuple as a small unified
    preview — at most `max_lines` total visible removed+added rows,
    with a "… N more" footer when truncated.

    Pure removals from the old side render as `-` rows; pure additions
    from the new side render as `+` rows. Common context lines are
    elided — this is a change preview, not a diff viewer.
    """
    path, old_text, new_text = diff
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    # Trim a common prefix and suffix so we show only the changed
    # window — cheap heuristic; sufficient for transcript previews.
    head = 0
    while (head < len(old_lines) and head < len(new_lines)
           and old_lines[head] == new_lines[head]):
        head += 1
    tail = 0
    while (tail < len(old_lines) - head
           and tail < len(new_lines) - head
           and old_lines[len(old_lines) - 1 - tail]
               == new_lines[len(new_lines) - 1 - tail]):
        tail += 1
    removed = old_lines[head:len(old_lines) - tail]
    added = new_lines[head:len(new_lines) - tail]

    body = Text()
    body.append(f"  ┌ {path}\n", style=colors.muted)
    shown = 0
    for line in removed:
        if shown >= max_lines:
            break
        body.append(f"  │ -", style=colors.err)
        body.append(f" {line}\n", style=colors.err)
        shown += 1
    for line in added:
        if shown >= max_lines:
            break
        body.append(f"  │ +", style=colors.ok)
        body.append(f" {line}\n", style=colors.ok)
        shown += 1
    elided = max(0, (len(removed) + len(added)) - shown)
    if elided > 0:
        body.append(f"  │ … {elided} more line"
                    f"{'s' if elided != 1 else ''}\n",
                    style=colors.muted)
    body.append("  └", style=colors.muted)
    return body


_PLAN_STATUS_GLYPH = {
    "completed": "●",
    "in_progress": "◐",
    "pending": "○",
}


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
        glyph = _PLAN_STATUS_GLYPH.get(entry.status, "○")
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
        # Suppress the parenthetical hint when it duplicates the name —
        # ACP titles are often the filename itself, so we'd otherwise
        # render "📖 target.txt(target.txt)".
        arg = f"({hint})" if hint and hint != ev.name else ""
        return Text.assemble((f"{icon} ", colors.accent), f"{ev.name}{arg}")
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
        secs = (ev.duration_ms or 0) / 1000
        parts = [f"done in {secs:.1f}s"]
        if ev.cost_usd is not None and ev.cost_usd > 0:
            parts.append(f"${ev.cost_usd:.4f}".rstrip("0").rstrip("."))
        # Only surface stop_reason when it's something the user should
        # notice — end_turn is the boring happy path.
        if ev.stop_reason and ev.stop_reason != "end_turn":
            parts.append(ev.stop_reason)
        return Text(f"── {' · '.join(parts)} ──", style=colors.muted)
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
