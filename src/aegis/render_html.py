"""HTML renderer — sibling to ``aegis.render.render_event``. Emits a
self-contained, escaped HTML fragment per event, mirroring the TUI's
rendering semantics via the shared ``aegis.render_shared`` helpers. Colors
are applied by CSS classes whose values come from the theme's
``to_css_variables()`` output, so this renderer takes no palette argument.
"""
from __future__ import annotations

from html import escape

from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, Event, Result, SystemInit,
    ToolResult, ToolUse, Unknown,
)
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, describe_tool, diff_window, result_parts,
)


def render_event_html(ev: Event) -> str | None:
    """Map one typed event to an HTML fragment, or None when it has no
    visible representation."""
    if isinstance(ev, AssistantText):
        text = ev.text.strip()
        if not text:
            return None
        return f'<div class="assistant-text">{escape(text)}</div>'

    if isinstance(ev, AssistantThinking):
        body = (ev.text or "").strip()
        if not body:
            return '<div class="thinking muted">✻ Thinking…</div>'
        return (f'<div class="thinking muted"><em>✻ '
                f'{escape(body)}</em></div>')

    if isinstance(ev, ToolUse):
        icon = KIND_ICON.get(ev.kind or "", "⏺")
        desc = describe_tool(ev.name, ev.raw_input, ev.summary, ev.locations)
        return (f'<div class="tool-use">'
                f'<span class="icon">{icon}</span> '
                f'<span class="tool-desc">{escape(desc)}</span></div>')

    if isinstance(ev, ToolResult):
        if ev.diff is not None and not ev.is_error:
            return _diff_html(ev.diff)
        first = ev.text.splitlines()[0] if ev.text.strip() else ""
        if len(first) > 100:
            first = first[:100] + "…"
        cls = "error" if ev.is_error else "ok"
        return (f'<div class="tool-result {cls}">└ '
                f'<span class="status">{cls}</span> '
                f'{escape(first)}</div>')

    if isinstance(ev, AgentPlan):
        return _plan_html(ev)

    if isinstance(ev, Result):
        inner = escape(" · ".join(result_parts(ev)))
        return f'<div class="result-sep">── {inner} ──</div>'

    if isinstance(ev, (SystemInit, Unknown)):
        return None
    return None


def _diff_html(diff: tuple[str, str, str]) -> str:
    path, old_text, new_text = diff
    removed, added, elided = diff_window(old_text, new_text)
    rows = [f'<div class="diff-head">┌ {escape(path)}</div>']
    for line in removed:
        rows.append(f'<div class="diff-row removed">- {escape(line)}</div>')
    for line in added:
        rows.append(f'<div class="diff-row added">+ {escape(line)}</div>')
    if elided > 0:
        s = "s" if elided != 1 else ""
        rows.append(f'<div class="diff-more">… {elided} more line{s}</div>')
    return f'<div class="tool-result diff">{"".join(rows)}</div>'


def _plan_html(plan: AgentPlan) -> str:
    total = len(plan.entries)
    if total == 0:
        return '<div class="agent-plan muted">📋 (no plan)</div>'
    done = sum(1 for e in plan.entries if e.status == "completed")
    rows = [f'<div class="plan-head">📋 Plan — {done}/{total} done</div>']
    for entry in plan.entries:
        glyph = PLAN_STATUS_GLYPH.get(entry.status, "○")
        prio = f" {entry.priority}" if entry.priority in ("high", "low") else ""
        rows.append(
            f'<div class="plan-row {entry.status}{prio}">'
            f'<span class="glyph">{glyph}</span> '
            f'{escape(entry.content)}</div>')
    return f'<div class="agent-plan">{"".join(rows)}</div>'
