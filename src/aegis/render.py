from __future__ import annotations

from rich import box
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from dataclasses import replace

from aegis.events import (
    AgentPlan,
    AssistantText,
    AssistantThinking,
    ToolUse,
    ToolResult,
    Result,
    SystemInit,
    Unknown,
    UserMessage,
    Event,
)
from aegis.comms.descriptors import aegis_glyph
from aegis.render_shared import (
    PLAN_STATUS_GLYPH,
    diff_window,
    result_digest,
    result_parts,
    tool_glyph,
    tool_label,
)

# Per-tool-call spinner (mirrors the turn-level WorkingIndicator glyphs).
_TOOL_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# A left bar and nothing else — Rich's Box is 8 rows of
# (left, horizontal, cross, right). A full box around every side note
# would fence the transcript; a single thin rule down the left edge reads
# as a margin note, which is what these are. `box.MINIMAL` was the first
# try and draws its verticals as spaces, so the "subtle border" was
# invisible and only the background did any work.
_ASIDE_BOX = box.Box("    \n▏   \n    \n▏   \n    \n    \n▏   \n    \n")

# A heavier left edge than the aside's, in the operator's own accent. The
# two blocks share proportions so they read as siblings; they must not
# share a surface, because an aside means "not the conversation" and the
# operator's message is the conversation.
_USER_BOX = box.Box("    \n┃   \n    \n┃   \n    \n    \n┃   \n    \n")


def _aside(parts, colors, border: str | None = None) -> Panel:
    """A block that sits *in* the transcript but is not the conversation.

    A `/btw` side note and an `@peer` answer both render their body as
    Markdown — which is exactly how agent prose renders — so without a
    surface of their own they read as the agent talking. That is the one
    thing they must never look like: a side note is a third voice, and an
    `@peer` answer belongs to a different session entirely.

    The separation is the theme's raised `panel` background plus a quiet
    `rule` border, rather than a louder colour. An aside should be easy to
    tell apart when you look at it and easy to ignore when you are not —
    a saturated tint would win attention from the conversation it is
    supposed to sit beside.
    """
    return Panel(
        Group(*parts),
        box=_ASIDE_BOX,
        border_style=border or colors.rule,
        style=f"on {colors.panel}",
        padding=(0, 1),
        expand=True,
    )


def render_deferred(
    label: str,
    subject: str,
    elapsed: float,
    colors,
    *,
    frame: int = 0,
    cancelled: bool = False,
    cancel_note: str = "",
) -> Panel:
    """The block a deferred command occupies while it runs, and after it is
    cancelled.

    Running, it echoes the subject: by the time a 12-17s call returns you
    have forgotten which side question you asked, and a spinner with no
    subject is just anxiety.

    Cancelled, it is a muted tombstone carrying the command's own
    ``cancel_note`` — a tombstone rather than a removal, because ESC
    silently deleting something you can see reads as a glitch, and this
    block is the only record that you spent anything at all.

    No cost is shown on the cancelled line. A cancelled call returns no
    usage, so we do not know what it cost, and inventing a number for a
    line whose entire purpose is honesty about price would be worse than
    the omission. Elapsed is what we actually know.

    Wrapped in the same ``_aside`` surface the answer will land in, so the
    block does not jump when it resolves — only its contents change.
    """
    line = Text()
    if cancelled:
        line.append(f"{label} ", style=f"bold italic {colors.muted}")
        line.append(f"· {cancel_note} · {_fmt_dur(elapsed)}", style=colors.muted)
        return _aside([line], colors)
    line.append(f"{_TOOL_SPINNER[frame % len(_TOOL_SPINNER)]}  ", style=colors.working)
    line.append(f"{label} ", style=f"bold italic {colors.accent}")
    if subject:
        line.append(f"· {subject} ", style=colors.muted)
    line.append(f"· {_fmt_dur(elapsed)}", style=colors.muted)
    return _aside([line], colors)


def _fmt_dur(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s"


_ELAPSED_W = 7  # "  12.4s" / "  3m04s" — the widest _fmt_dur output


# The icon's pulse while a call is in flight. The ticker runs at 10 Hz, so
# five frames on and five off reads as a 1 Hz blink — a cursor's rhythm, not
# a strobe.
_PULSE_FRAMES = 5

_LABEL_FRAC = 0.42  # share of the row the label may occupy
_LABEL_MIN = 16
_LABEL_MAX = 48


class _ToolRow:
    """One tool call as three columns: what it was, how it went, how long.

    The middle column is the point of the row. A transcript is read to find
    out what came *back* — what passed, what failed, how many matches — and
    the label is only there to say which call that was. So the label gets a
    bounded column and gives way first; the result keeps the rest. The line
    used to be one string truncated from the right, which clipped the result
    and kept the argument, exactly backwards (Alex, 2026-09-21).

    Both columns are laid out at paint time. The only width a caller can
    measure is the transcript's, and a block gets less than that — its own
    padding, and the scrollbar when there is one — so padding to a width
    passed in put the stamp one cell past the edge and wrapped it onto a row
    of its own, which is the one thing this line may never do. Caught by
    driving the real app, not by a unit test, which had handed itself the
    same wrong number twice.

    A ``Table.grid`` does this too and reads better, but it costs 0.49 ms/row
    against 0.29 here (width 100, 4,000 rows, zion 2026-09-18) — 1.7x, on the
    renderable a transcript has most of.
    """

    __slots__ = ("label", "verdict", "stamp")

    def __init__(self, label: Text, verdict: Text, stamp: Text) -> None:
        self.label = label
        self.verdict = verdict
        self.stamp = stamp

    def __rich_console__(self, console, options):
        body_w = max(1, options.max_width - _ELAPSED_W)
        label_w = min(_LABEL_MAX, max(_LABEL_MIN, int(body_w * _LABEL_FRAC)))
        label_w = min(label_w, body_w)

        row = self.label.copy()
        # The label Text carries end="" so the no-column path can append to
        # it. Here it is the whole row, and a row that does not end has no
        # height: Textual mounted the block and painted nothing.
        row.end = "\n"
        # Truncate before padding: pad_right on an already-too-long line
        # would not shorten it.
        row.truncate(label_w, overflow="ellipsis")
        row.pad_right(max(0, label_w - row.cell_len))

        verdict = self.verdict.copy()
        verdict.truncate(max(0, body_w - label_w), overflow="ellipsis")
        row.append_text(verdict)
        row.pad_right(max(0, body_w - row.cell_len))
        row.append_text(self.stamp)
        yield row

    def __rich_measure__(self, console, options):
        from rich.measure import Measurement

        return Measurement(_ELAPSED_W + 2, options.max_width)


def render_tool_use(
    ev,
    colors,
    *,
    elapsed: float | None = None,
    running: bool = False,
    frame: int = 0,
    result=None,
    column: bool = False,
) -> RenderableType:
    """One tool call, one row: glyph + label, then the verdict and its
    digest, then the elapsed time in a right-hand column.

    Exactly one row is the contract. The full input and output live behind
    the click, in ``ToolDetailScreen`` — a transcript that grew a dozen rows
    per call could not be skimmed, and the rows it grew carried the first
    100 characters of the output, which is where a Read shows its imports
    and a Bash shows a progress bar.

    ``column=True`` puts the elapsed time in a right-hand column that lines
    up down the whole transcript; the live pane asks for it. Off, the stamp
    just trails the text, which is what a bare Console.print gets.
    """
    icon = tool_glyph(ev.name, ev.kind, ev.raw_input)
    # The LABEL, not describe_tool: which call this was, with none of what
    # was fed to it. A row exists to show the result.
    desc = tool_label(ev.name, ev.raw_input, ev.summary, ev.locations)
    # A call into the aegis layer wears the layer's own colour, so a
    # transcript shows at a glance where agents were talking to each other.
    style = colors.comms if aegis_glyph(ev.name, ev.raw_input or {}) else colors.accent
    if running:
        # The icon IS the running indicator: it pulses in flight and settles
        # the moment the call returns. A terminal's own blink attribute
        # (SGR 5) is ignored or mangled by plenty of terminals, so the pulse
        # is ours — `dim` for half of each cycle, off the same 10 Hz ticker
        # that moves the elapsed digits, which lands on ~1 Hz.
        if (frame // _PULSE_FRAMES) % 2:
            style = f"dim {style}"

    line = Text(no_wrap=True, overflow="ellipsis", end="")
    line.append(f"{icon} ", style=style)
    line.append(desc)

    if running:
        # Nothing in the middle column while it runs. That space is where
        # the result lands, and a spinner sitting in it is one more thing
        # to look past on a row whose whole job is the result.
        mark, vstyle = "", colors.muted
    elif result is None:
        mark, vstyle = "", colors.muted
    elif result.is_error:
        mark, vstyle = "✗", colors.err
    else:
        mark, vstyle = "✓", colors.ok
    digest = "" if running else result_digest(ev.name, result)

    verdict = Text(no_wrap=True, overflow="ellipsis", end="")
    if mark:
        verdict.append(mark, style=vstyle)
    if digest:
        verdict.append(f" {digest}" if mark else digest, style=colors.muted)

    if elapsed is None:
        if verdict.cell_len:
            line.append("  ")
            line.append_text(verdict)
        return line
    stamp = Text(_fmt_dur(elapsed).rjust(_ELAPSED_W), style=colors.muted, no_wrap=True)
    if not column:
        if verdict.cell_len:
            line.append("  ")
            line.append_text(verdict)
        line.append("  ")
        line.append_text(stamp)
        return line
    return _ToolRow(line, verdict, stamp)


def _render_diff(diff: tuple[str, str, str], colors, max_lines: int = 6) -> "Text":
    """Render a (path, old_text, new_text) tuple as a small unified
    preview using the shared diff windowing — at most `max_lines` total
    visible removed+added rows, with a "… N more" footer when truncated.
    """
    path, old_text, new_text = diff
    removed, added, elided = diff_window(old_text, new_text, max_lines)

    body = Text()
    body.append(f"  ┌ {path}\n", style=colors.muted)
    for line in removed:
        body.append("  │ -", style=colors.err)
        body.append(f" {line}\n", style=colors.err)
    for line in added:
        body.append("  │ +", style=colors.ok)
        body.append(f" {line}\n", style=colors.ok)
    if elided > 0:
        body.append(
            f"  │ … {elided} more line{'s' if elided != 1 else ''}\n",
            style=colors.muted,
        )
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
    body.append(f"📋 Plan — {done}/{total} done\n", style=f"bold {colors.accent}")
    for entry in plan.entries:
        glyph = PLAN_STATUS_GLYPH.get(entry.status, "○")
        glyph_style = (
            colors.ok
            if entry.status == "completed"
            else colors.accent
            if entry.status == "in_progress"
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
            if (
                buf is not None
                and type(buf) is type(ev)
                and buf.message_id == ev.message_id
            ):
                buf = replace(buf, text=buf.text + ev.text, usage=ev.usage or buf.usage)
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


def renders_to_nothing(ev: Event) -> bool:
    """Whether ``render_event(ev)`` would return None — answered without
    rendering anything.

    Replay uses this to decide which events deserve a transcript block
    without paying for the renderable: constructing a `Markdown` parses
    eagerly, and doing that for every historical event cost seconds on a
    long log. Mirrors render_event's None cases exactly — keep the two in
    step.
    """
    if isinstance(ev, (AssistantText, UserMessage)):
        return not ev.text.strip()
    return not isinstance(
        ev, (AssistantThinking, ToolUse, ToolResult, AgentPlan, Result)
    )


def render_event(
    ev: Event, colors, *, age_s: float | None = None
) -> RenderableType | None:
    """Map one typed event to a Rich renderable (themed), or None.

    ``age_s`` is how long ago the event landed; only the turn terminator
    uses it, and only the live pane passes it (see
    ``ConversationPane.refresh_result_age``)."""
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

        approx = (
            ev.token_estimate
            if ev.token_estimate > 0
            else max(1, len((ev.text or "").strip()) // 4)
        )
        return Text(
            f"💭 thought · ~{_fmt_tokens(approx)} tok", style=f"italic {colors.muted}"
        )
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
            return Text.assemble(("  └ ", colors.muted), ("error ", colors.err), first)
        return Text.assemble(("  └ ", colors.muted), ("ok ", colors.ok), first)
    if isinstance(ev, AgentPlan):
        return _render_agent_plan(ev, colors)
    if isinstance(ev, Result):
        return Text(
            f"── {' · '.join(result_parts(ev, age_s=age_s))} ──", style=colors.muted
        )
    if isinstance(ev, UserMessage):
        # Same line the live pane mounts at send time, so a reopened
        # conversation is indistinguishable from the one you were just in.
        # Width is unknown here, so the tint band ends at the text rather
        # than running full-width; the live pane passes its own width.
        text = ev.text.strip()
        return render_user_block(text, colors) if text else None
    if isinstance(ev, (SystemInit, Unknown)):
        return None
    return None


def render_user_block(text: str, colors, width: int | None = None) -> Panel:
    """The operator's own message: an accent `›` header over a Markdown body.

    Markdown, because a prompt is prose and carries fenced blocks, lists and
    backticks. The cost is that `snake_case` loses its underscores and
    `**/*.py` turns bold — a trade taken deliberately (2026-09-21
    input-suggestion spec), so a mangled glob is a known cost, not a defect.

    Deliberately not ``_aside``: that surface means "in the transcript but
    not the conversation", and this is the conversation. Same proportions,
    its own box and colours.

    ``width`` is accepted and ignored. The panel expands to its container,
    which is what the old single-line band had to fake by padding.
    """
    # A grid, not a Group: the glyph belongs on the body's first line, and
    # stacking it would spend a whole row of the transcript per message.
    # The per-row cost a Table.grid carries over a plain Text (see
    # ``_ToolRow``) does not matter here — there is one of these per turn,
    # against thousands of tool rows.
    from rich.table import Table

    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1, no_wrap=True)
    grid.add_column(ratio=1, overflow="fold")
    grid.add_row(Text("›", style=f"bold {colors.user}"), Markdown(text))
    return Panel(
        grid,
        box=_USER_BOX,
        border_style=colors.user,
        style=f"on {colors.user_bg}",
        padding=(0, 1),
        expand=True,
    )


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


def render_side_note(note, colors) -> Panel:
    """Visible block for a `/btw` side note.

    The answer is rendered as Markdown; the error is not. A model asked a
    technical question answers in markdown, and you should not be reading
    the asterisks. An error is not model prose — it is aegis speaking a
    fixed sentence, and it carries the alternative the operator has to act
    on. Markdown imposes its own styling, which is exactly why it is right
    for the answer and wrong for the failure line's `colors.error` tint.

    Its own treatment, because it is neither a user line nor agent output
    — it is a third voice, and one that is not part of the conversation.
    The footer carries model, latency and cost: a side note is a paid call
    and the price should be visible.

    Transient by design. This block goes into the pane's ``_history`` (so
    scrolling keeps it) and is never appended to the session log, so it
    does not survive a reload and never enters the window a later `/btw`
    assembles. Side notes do not compound.
    """
    tint = colors.error if not note.ok else colors.accent
    parts: list[RenderableType] = [Text("btw", style=f"bold italic {tint}")]
    if note.ok:
        parts.append(Markdown(note.answer))
    else:
        parts.append(Text(note.error or "no answer", style=tint))
    if note.ok and note.needs_more:
        parts.append(
            Text(
                f"  answered from {note.header} — /fork if you want it to "
                f"actually go look.",
                style=f"italic {colors.working}",
            )
        )
    if note.footer:
        parts.append(Text(note.footer, style=colors.muted))
    return _aside(parts, colors)


def render_recap(recap, colors, *, session: bool = False) -> Panel:
    """Visible block for a recap.

    The block itself lands in the pane's ``_history``; the recap line and
    its attention category are persisted as a ``RecapNote``, which the
    recap window skips. That skip is the mechanism behind "the recap never
    enters the agent's context" — and it is also what stops recaps
    compounding, since a recap in the window the *next* recap assembles
    would make every summary after it summarize its own summaries.

    On the ok path the header names the turn's attention category, and
    the border takes its colour unless the turn is simply ``done``.

    The turn recap's body is the outcome with the task on a muted line
    under it; ``session`` (``/recap``) draws the labelled task / outcome /
    next list instead.

    Markdown on the ok path only, for the reason ``render_side_note``
    gives: the text is model prose, but an error is aegis speaking a fixed
    sentence and keeps its ``colors.error`` tint.
    """
    tint = colors.error if not recap.ok else colors.accent
    header = Text("recap", style=f"bold italic {tint}")
    border = None
    if recap.ok and recap.attention:
        from aegis.attention import LABELS, mark, style_for

        header.append(" · ", style=colors.muted)
        header.append_text(Text.from_markup(mark(recap.attention, colors)))
        header.append(
            f" {LABELS.get(recap.attention, recap.attention)}",
            style=style_for(recap.attention, colors),
        )
        if recap.attention != "done":
            border = style_for(recap.attention, colors)
    parts: list[RenderableType] = [header]
    if recap.ok and session:
        parts.append(Markdown(recap.block))
    elif recap.ok:
        parts.append(Markdown(recap.text))
        if recap.task:
            parts.append(Text(recap.task, style=colors.muted))
    else:
        parts.append(Text(recap.error or "no answer", style=tint))
    if recap.footer:
        parts.append(Text(recap.footer, style=colors.muted))
    return _aside(parts, colors, border)


def render_peer_answer(answer, colors) -> Panel:
    """Visible block for an `@peer` answer.

    The header leads with the target and is the first renderable of the
    returned Group: in a pane full of transient blocks the first token is
    how you tell "beta answered this" from "this is my own agent talking".

    Markdown on the ok path only, for the reason given in
    ``render_side_note`` — a refusal like "beta is mid-turn. Wait for it to
    finish, or /enqueue the task instead." is aegis speaking, and it keeps
    its `colors.error` tint.

    Transient in *this* pane, exactly as a side note is — it lands in
    ``_history`` and is never appended to this session's log, so the
    agent you are sitting with neither sees it nor pays for it. The same
    answer is a real turn in the peer's own transcript, which is where it
    belongs: a log holding a question with no answer would corrupt every
    window later assembled from it.
    """
    tint = colors.error if not answer.ok else colors.accent
    parts: list[RenderableType] = [
        Text(f"@{answer.target or '?'} ", style=f"bold italic {tint}")
    ]
    if answer.ok:
        parts.append(Markdown(answer.answer))
    else:
        parts.append(Text(answer.error or "no answer", style=tint))
    if answer.footer:
        parts.append(Text(answer.footer, style=colors.muted))
    return _aside(parts, colors)


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
        head = f"from {msg.sender} · task#{msg.task_id} · {status} · {msg.timestamp}"
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
        line.append(f"  … ({remaining} more line{s})\n", style=colors.muted)
    return line
