from __future__ import annotations

import contextlib
import re
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.drivers.base import HarnessSession
from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, Result, SessionMeta,
    ThinkingTokens, ToolResult, ToolUse, UserMessage,
)
from aegis.render import (
    coalesce_chunks, render_event, render_inbox_block, render_tool_use,
    render_user_line, renders_to_nothing,
)
from aegis.render_shared import FileTarget, file_target, format_age
from aegis.state.session_log import EventReplay, make_session_log_observer
from aegis.tui.state import AgentState
from aegis.tui.palette import CommandPalette
from aegis.tui.pending import Chip, PendingStrip
from aegis.tui.monitor_strip import MonitorStrip
from aegis.tui.plan_dock import PlanDock
from aegis.tui.plan_strip import PlanStrip
from aegis.tui.strip import QueueStrip
from aegis.tui.widgets import GrowingInput, StatusBar
from aegis.transcript_constants import (  # noqa: F401  (re-exported)
    N_MAX, REPLAY_TAIL, EVICT_BATCH, LOAD_BATCH, STICKY_EPS, LOAD_MORE_EPS,
    DEBOUNCE_S, STREAM_REPAINT_S,
)


# Names of the tool that dispatches a subagent. Claude Code has used both
# across versions ("Task" historically, "Agent" as of 2.1.x); match both so
# subagent events group into a box regardless of the running CLI's naming.
_SUBAGENT_TOOLS = frozenset({"Task", "Agent"})


def fold_plan_events(events: list) -> list:
    """Collapse AgentPlan revisions to the latest, per plan owner.

    The plan is a mutating object: every TaskCreate / TaskUpdate /
    TaskList re-emits the whole thing, so a 21-task plan arrives as ~50
    cumulative events. Appending each would trade N anonymous tool rows
    for N plan blocks — the same noise wearing a hat.

    The surviving block keeps the position where the plan first appeared;
    the strip is the live surface, this is the record. Keyed by
    parent_tool_use_id so a subagent's plan does not overwrite its
    parent's.
    """
    out: list = []
    slot: dict[str | None, int] = {}
    for ev in events:
        if isinstance(ev, AgentPlan):
            key = ev.parent_tool_use_id
            if key in slot:
                out[slot[key]] = ev
                continue
            slot[key] = len(out)
        out.append(ev)
    return out

_BLOCK_TOOLTIP = ("click to copy | ctrl+click to open here | "
                  "alt+click to open natively")



@dataclass(slots=True)
class BlockRecord:
    """One transcript entry. Mutable so streaming aggregation can update
    in place. Mirrors the arguments passed to CopyableBlock so older
    blocks can be reconstructed on scroll-up.

    ``renderable`` may be None when ``events`` is set: replay stores the
    source events and renders them only if the block is actually mounted.
    Rendering everything up front cost 4.35s on a 25MB log to paint ten
    blocks — `Markdown` parses in its constructor, and the other 99% of the
    renderables were thrown away.
    """
    renderable: RenderableType | None
    payload: str
    tight: bool
    tool_call_id: str | None = None
    events: list | None = None
    file_target: FileTarget | None = None

    def materialize(self, palette) -> RenderableType:
        """The renderable, rendering the deferred events on first use."""
        if self.renderable is None:
            from aegis.render import render_event
            rends = [r for r in (render_event(ev, palette)
                                 for ev in (self.events or []))
                     if r is not None]
            self.renderable = (rends[0] if len(rends) == 1
                               else Group(*rends) if rends else Text(""))
        return self.renderable


@dataclass(slots=True)
class _ToolTrack:
    """Live state for one tool call: enough to re-render its block with a
    ticking timer while running, a frozen duration once done, and the full
    args when expanded."""
    ev: object                      # the ToolUse event
    idx: int                        # history index of its block
    start: float                    # time.monotonic() at dispatch
    done: bool = False
    elapsed: float | None = None    # frozen duration once done
    result_r: RenderableType | None = None
    expanded: bool = False


@dataclass(slots=True)
class _DeferredTrack:
    """Live state for the one running deferred command: enough to re-render
    its placeholder with a ticking timer, replace it with the result, or
    tombstone it on cancel.

    One per pane, not one per command. The primitive is general — `/btw`
    and `@peer` both use it — and a single slot keeps ESC unambiguous:
    two spinners racing in one pane would leave the cancel key with no
    defensible answer about which one it kills.
    """
    idx: int                        # history index of its block
    start: float                    # time.monotonic() at dispatch
    label: str                      # "btw", "@beta"
    subject: str                    # the question, echoed while it runs
    cancel_note: str                # already resolved against parsed args
    worker: object = None
    done: bool = False
    elapsed: float | None = None


@dataclass(slots=True)
class _ResultBlock:
    """The newest turn terminator, tracked so its "x ago" stays honest."""
    block: object                   # the CopyableBlock
    ev: object                      # the Result event
    idx: int                        # history index of its record
    ended_at: float                 # time.time() when it landed
    shown: str = ""                 # last age string rendered


def replay_blocks(replay: EventReplay, colors=None) -> list[RenderableType]:
    """Render replay events as a list of Rich renderables, in order,
    using the live render path. Appends a ⚠ interrupted marker if
    replay.interrupted, and a ⚠ damaged-records marker if part of the
    log was unreadable. Returns an empty list for an empty replay.
    """
    if colors is None:
        from aegis.tui.themes import INK, aegis_colors
        colors = aegis_colors(INK)
    blocks: list[RenderableType] = []
    for ev in coalesce_chunks(replay.events):
        r = render_event(ev, colors)
        if r is None:
            continue
        blocks.append(r)
    if replay.damaged:
        # Say it out loud: a silently shortened transcript reads as a
        # conversation that was always this short.
        note = (f"⚠ {replay.damaged} damaged record(s) skipped"
                + (f" · {replay.recovered} recovered" if replay.recovered
                   else ""))
        blocks.append(Text(note, style="yellow"))
    if replay.interrupted:
        blocks.append(Text("⚠ interrupted", style="yellow"))
    return blocks


# ---------- WorkingIndicator -----------------------------------------

# Single-row indicator that lives between the transcript and the
# status bar. Hidden by default (collapses to 0 height); becomes
# visible while the pane is in AgentState.working with:
#
#   ⠋  Pondering…  3.2s
#
# The verb rotates every ~5s to keep the eye amused during long runs.
# The spinner glyph cycles at ~100ms; the timer ticks at the same rate.

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_VERBS: tuple[str, ...] = (
    "Thinking", "Pondering", "Cogitating", "Ruminating",
    "Brewing", "Marinating", "Percolating", "Stewing",
    "Distilling", "Conjuring", "Architecting", "Synthesizing",
    "Crystallizing", "Untangling", "Deliberating", "Forging",
    "Composing", "Convoluting", "Spelunking", "Wrangling",
    "Brainstorming", "Plotting", "Scheming", "Reticulating",
)


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _thought_summary(elapsed_s: float, char_len: int, palette,
                     token_estimate: int = 0) -> "Text":
    """A completed reasoning block renders as a compact one-liner rather than
    a wall of streamed reasoning: ``💭 thought · 0:42 · ~1.2k tok``. Prefer
    the harness-reported thinking-token estimate (Claude redacts the
    reasoning text, so char_len is 0); fall back to a ~4-chars/token
    heuristic for harnesses that stream the text instead. The full reasoning
    is preserved as the block's copy payload."""
    from aegis.tui.metrics import _fmt_tokens
    approx = token_estimate if token_estimate > 0 else max(1, char_len // 4)
    return Text(
        f"💭 thought · {_fmt_elapsed(elapsed_s)} · ~{_fmt_tokens(approx)} tok",
        style=f"italic {palette.muted}")


class WorkingIndicator(Static):
    """Inline 'agent is working' row. Hidden (0-height) when idle,
    one row when active. Cycles spinner glyph + verb + elapsed timer."""

    DEFAULT_CSS = """
    WorkingIndicator { height: 1; padding: 0 1; margin-bottom: 1;
                       background: transparent;
                       color: $foreground 50%; text-style: italic; }
    """

    def __init__(self, palette) -> None:
        super().__init__("", id="working-indicator")
        self._palette = palette
        self._started_at: float | None = None
        self._frame = 0
        self._verb_idx = 0
        self._tick_timer = None
        self._verb_timer = None

    @property
    def is_active(self) -> bool:
        return self._started_at is not None

    def start(self, *, animate: bool = True) -> None:
        # Idempotent: cancel any prior timers first so re-starting a
        # lingering indicator (chained / self-woken turn) neither leaks
        # timers nor leaves a frozen spinner.
        self._cancel_timers()
        self.add_class("-active")
        self._started_at = time.monotonic()
        self._frame = 0
        self._verb_idx = random.randrange(len(_VERBS))
        self._refresh()
        # Spinner + timer redraw at 100ms; verb rotates every 5s — but only
        # when visible. A background pane freezes on its last frame (see
        # set_animating) so it adds no per-tick pump load.
        if animate:
            self._start_timers()

    def _start_timers(self) -> None:
        self._tick_timer = self.set_interval(0.1, self._tick)
        self._verb_timer = self.set_interval(5.0, self._rotate_verb)

    def _cancel_timers(self) -> None:
        for t in (self._tick_timer, self._verb_timer):
            if t is not None:
                with contextlib.suppress(Exception):
                    t.stop()
        self._tick_timer = None
        self._verb_timer = None

    def set_animating(self, on: bool) -> None:
        """Toggle the redraw timers without touching active/elapsed state, so
        a hidden pane's spinner freezes and resumes on show. No-op unless the
        indicator is active."""
        if not self.is_active:
            return
        running = self._tick_timer is not None
        if on and not running:
            self._refresh()
            self._start_timers()
        elif not on and running:
            self._cancel_timers()

    def stop(self) -> None:
        self.remove_class("-active")
        self._started_at = None
        self._cancel_timers()
        self.update("")

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self._refresh()

    def _rotate_verb(self) -> None:
        self._verb_idx = (self._verb_idx + 1) % len(_VERBS)
        self._refresh()

    def _refresh(self) -> None:
        if self._started_at is None:
            return
        spinner = _SPINNER_FRAMES[self._frame]
        verb = _VERBS[self._verb_idx]
        elapsed = _fmt_elapsed(time.monotonic() - self._started_at)
        # layout=False: this fires at 10 Hz for the whole duration of every
        # turn, and the indicator is `height: 1` in its own CSS — its size
        # cannot change. The default (layout=True) made the spinner rebuild
        # the entire compositor map ten times a second.
        self.update(Text(
            f"{spinner}  {verb}…  {elapsed}",
            style=f"italic {self._palette.muted}",
        ), layout=False)


def _extract_backtick_tokens(text: str) -> list[str]:
    """Return unique strings enclosed in single backticks, in first-seen order.

    Dedup matters: tokens feed a chooser whose options key on the token
    string. Repeated filenames in one block would otherwise collide on id.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in re.findall(r"`([^`\n]+)`", text):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


class CopyableBlock(Static):
    """One transcript cell — hover tints, click copies its text payload.

    The visible content can be updated in place via ``update_content``
    so that streaming text events (token-by-token AssistantText /
    AssistantThinking) accumulate into a single block rather than
    fragmenting into many short ones.

    Renders its own content rather than wrapping a child ``Static``.
    Textual rebuilds the entire compositor map on any layout change, and
    the map is walked on every keystroke, scroll line and streamed
    delta; a wrapper child doubled the widget count and with it the
    per-block term. That term is ~0.033 ms per mounted block of a real
    layout pass, so this is worth a few ms at a full window — not the
    ~180 ms first claimed, which was benchmark harness cost (see
    docs/superpowers/specs/2026-07-29-tui-performance-audit.md, round 3).
    Still right, just smaller than advertised.
    """

    DEFAULT_CSS = """
    CopyableBlock { height: auto; padding: 0 1; margin-bottom: 1;
                    background: $background; }
    /* Tight blocks have no margin below — used to glue a tool call
       (⏺) to its result (└ ok) so they read as one paired unit. */
    CopyableBlock.-tight { margin-bottom: 0; }
    CopyableBlock:hover { background: $surface; }
    """

    class ToolExpandToggle(Message):
        """A tool-call block was clicked — toggle its collapsed args."""
        def __init__(self, tool_call_id: str) -> None:
            super().__init__()
            self.tool_call_id = tool_call_id

    def __init__(self, renderable: RenderableType,
                 text_payload: str, *, tight: bool = False,
                 tool_call_id: str | None = None,
                 file_target: FileTarget | None = None,
                 remote_path: str | None = None,
                 host: str = "local") -> None:
        self._host = host
        # markup=False: the payloads are Rich renderables, and a raw str
        # payload must not be reinterpreted as Textual markup.
        super().__init__(renderable, markup=False,
                         classes="-tight" if tight else None)
        self._renderable = renderable
        self._text_payload = text_payload
        self._tool_call_id = tool_call_id
        self._file_target = file_target
        # A host-qualified path (``vps:/srv/app/x.py``) for a block whose
        # file lives on another machine. There is nothing to open here, so
        # ctrl+click copies this instead of opening the identically-named
        # local file — which would be a silent wrong answer.
        self._remote_path = remote_path
        # Resolved on demand: scanning the payload on every update made a
        # streaming message quadratic in its own length, and nothing reads
        # the tokens until you click or hover.
        self._tokens: list[str] | None = None
        # Textual tooltip floats above the widget on hover — no
        # layout shift, no extra row inside the block.
        tip = ("click to expand args" if tool_call_id is not None
               else "click to copy")
        if file_target is not None:
            tip = f"{tip} | ctrl+click to open the file"
        elif remote_path is not None:
            tip = f"{tip} | ctrl+click to copy {remote_path}"
        self.tooltip = tip

    def on_enter(self, _event) -> None:
        # Advertise the open gestures only when this block actually names
        # something openable. Resolved here rather than on every content
        # update: hovering is rare, streaming is not.
        if (self._tool_call_id is None and self._file_target is None
                and self.backtick_tokens):
            self.tooltip = _BLOCK_TOOLTIP

    def update_content(self, renderable: RenderableType,
                       text_payload: str, *, layout: bool = True) -> None:
        """Replace the block's content.

        ``layout=False`` when the new content provably occupies the same
        rows as the old — a running tool block rewriting its elapsed
        digits, say. A layout refresh rebuilds the whole compositor map,
        so a 10 Hz repaint that cannot change height must not ask for one.
        """
        self._renderable = renderable
        self._text_payload = text_payload
        self._tokens = None
        with contextlib.suppress(Exception):
            self.update(renderable, layout=layout)

    @property
    def backtick_tokens(self) -> list[str]:
        if self._tokens is None:
            self._tokens = _extract_backtick_tokens(self._text_payload)
        return self._tokens

    def text_payload(self) -> str:
        return self._text_payload

    def get_selection(self, selection):
        """Textual's default extracts text only when the widget renders a
        Text/Content (`Widget.get_selection`). `visualize()` converts a
        rich Text into Content, so user lines and tool lines already
        select — but assistant prose is a Markdown and a folded tool pair
        is a Group, both of which become a RichVisual and return None. The
        block already carries its own plain-text payload, so hand that
        over and the whole transcript becomes selectable."""
        return selection.extract(self._text_payload), "\n"

    def on_click(self, event: Click) -> None:
        # A Read/Write/Edit block already names its file exactly — ctrl+click
        # goes straight there, no token guessing. Checked before the
        # tool-call branch so a replayed block (no live tool_call_id, so no
        # args to expand) keeps the gesture.
        if event.ctrl and self._file_target is not None:
            self._open_tool_file()
            return
        # Off-host: the file is on another machine. Hand over the qualified
        # path rather than opening the local file of the same name.
        if event.ctrl and self._remote_path is not None:
            self.app.copy_to_clipboard(self._remote_path)
            with contextlib.suppress(Exception):
                self.app.notify(f"copied {self._remote_path}", timeout=2.0)
            return
        # Tool-call blocks toggle their collapsed args instead of copying.
        if self._tool_call_id is not None:
            self.post_message(self.ToolExpandToggle(self._tool_call_id))
            return
        # Token resolution walks the LOCAL tree, so it is meaningless — and
        # actively misleading — for a pane whose harness is elsewhere.
        if event.ctrl and self._host == "local" and self.backtick_tokens:
            self._open_file_from_tokens()
            return
        # Textual reports Alt as `meta` (SGR's bit 8). Shift is not usable
        # for a gesture: VTE terminals reserve it to bypass mouse reporting
        # entirely, so the app never sees a shift+click.
        if event.meta and self.backtick_tokens:
            self._open_natively_from_tokens()
            return
        if not self._text_payload:
            return
        try:
            self.app.copy_to_clipboard(self._text_payload)
        except Exception:
            return
        try:
            self.app.notify(
                f"copied {len(self._text_payload)} chars", timeout=1.5)
        except Exception:
            pass

    @work
    async def _open_tool_file(self) -> None:
        """Open the file a Read/Write/Edit call named, at its line.

        No picker and no fuzzy matching: the tool call already told us the
        exact path. Edit's line is found here rather than at render time —
        it costs a file read, and nothing needs it until you click.
        """
        from aegis.render_shared import anchor_line

        target = self._file_target
        opener = getattr(self.app, "_open_file_tab", None)
        if target is None or opener is None:
            return

        path = Path(target.path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            with contextlib.suppress(Exception):
                self.app.notify(f"{path.name}: not on disk here", timeout=2.0)
            return

        line = target.line
        if line is None and target.anchor:
            try:
                line = anchor_line(path.read_text(errors="replace"),
                                   target.anchor)
            except OSError:
                line = None
        await opener(path, line=line)

    @work
    async def _open_file_from_tokens(self) -> None:
        from aegis.tui.picker import (
            FilePickerModal, _TokenChooser, filter_path_tokens,
            resolve_unique_match)

        cwd = Path.cwd()
        indexer = getattr(self.app, "_file_indexer", None)
        paths = (indexer.paths
                 if (indexer is not None and indexer.ready) else [])
        tokens = filter_path_tokens(self.backtick_tokens, cwd, paths)
        if not tokens:
            with contextlib.suppress(Exception):
                self.app.notify("no path-like tokens here", timeout=1.5)
            return

        if len(tokens) == 1:
            token = tokens[0]
        else:
            token = await self.app.push_screen_wait(_TokenChooser(tokens))
            if token is None:
                return

        opener = getattr(self.app, "_open_file_tab", None)

        # Bypass the picker when the token resolves unambiguously.
        match = resolve_unique_match(token, paths)
        if match is not None:
            candidate = cwd / match
            if candidate.is_file() and opener is not None:
                await opener(candidate)
                return
        # Token might itself already be a file on disk (re-rooted from
        # an absolute path or directly indexable).
        direct = cwd / token
        if direct.is_file() and opener is not None:
            await opener(direct)
            return

        path = await self.app.push_screen_wait(FilePickerModal(prefill=token))
        if path is not None and opener is not None:
            await opener(path)

    @work
    async def _open_natively_from_tokens(self) -> None:
        """Alt+click: hand the token to the desktop's own handler.

        Same token resolution as ctrl+click, different destination — plus
        URLs, which aegis has nothing to do with but a browser does.
        """
        from aegis.tui.native_open import (
            is_url, open_native, refuse_reason)
        from aegis.tui.picker import _TokenChooser, filter_path_tokens

        def _notify(msg: str) -> None:
            with contextlib.suppress(Exception):
                self.app.notify(msg, timeout=2.0)

        if hasattr(self.app, "_remote_manager"):
            # The TUI is local but the paths are the daemon host's; opening
            # them here would hit whatever happens to sit at that path
            # locally, or nothing.
            _notify("native open is local-only (remote session)")
            return

        cwd = Path.cwd()
        indexer = getattr(self.app, "_file_indexer", None)
        paths = (indexer.paths
                 if (indexer is not None and indexer.ready) else [])
        urls = [t for t in self.backtick_tokens if is_url(t)]
        tokens = urls + filter_path_tokens(self.backtick_tokens, cwd, paths)
        if not tokens:
            _notify("nothing openable here")
            return

        if len(tokens) == 1:
            token = tokens[0]
        else:
            token = await self.app.push_screen_wait(_TokenChooser(tokens))
            if token is None:
                return

        if is_url(token):
            err = open_native(token)
            _notify(err or f"opening {token}")
            return

        target = Path(token)
        if not target.is_absolute():
            target = cwd / target
        if not target.exists():
            _notify(f"{token} is not on disk")
            return
        refusal = refuse_reason(target)
        if refusal is not None:
            _notify(refusal)
            return
        err = open_native(str(target))
        _notify(err or f"opening {target.name}")


class SubagentBox(Widget):
    """Collapsible container for one Task subagent's events. The header is the
    Task call; the body is the routed child events; the footer is the Task
    result. Counts as ONE transcript block — its children live inside."""

    DEFAULT_CSS = """
    SubagentBox { height: auto; padding: 0 1; margin-bottom: 1;
                  background: $background; }
    SubagentBox > .sa-header { height: auto; }
    SubagentBox > .sa-body { height: auto; padding: 0 0 0 2;
                             border-left: solid $surface; }
    SubagentBox:hover { background: $surface; }
    """

    collapsed: reactive[bool] = reactive(True)

    def __init__(self, header: RenderableType, header_payload: str,
                 palette, *, collapsed: bool = True) -> None:
        super().__init__()
        self._palette = palette
        self._header = header
        self._header_payload = header_payload
        self._children: list[BlockRecord] = []
        self._footer: RenderableType | None = None
        self._footer_payload = ""
        self.set_reactive(SubagentBox.collapsed, collapsed)

    def set_header(self, renderable: RenderableType, payload: str) -> None:
        self._header = renderable
        self._header_payload = payload
        self._refresh()

    def add_child(self, renderable: RenderableType, payload: str,
                  *, tight: bool = False) -> None:
        self._children.append(BlockRecord(renderable, payload, tight))
        self._refresh()

    def fold_child_result(self, renderable: RenderableType,
                          payload: str) -> bool:
        """Fold a tool result into the box's last child (mirror of the
        top-level tool pairing). False when there's no child to fold into."""
        if not self._children:
            return False
        rec = self._children[-1]
        rec.renderable = Group(rec.renderable, renderable)
        rec.payload = f"{rec.payload}\n{payload}"
        self._refresh()
        return True

    def close(self, renderable: RenderableType, payload: str) -> None:
        self._footer = renderable
        self._footer_payload = payload
        self._refresh()

    def toggle(self) -> None:
        self.collapsed = not self.collapsed

    def watch_collapsed(self, _old: bool, _new: bool) -> None:
        self._refresh()

    def text_payload(self) -> str:
        parts = [self._header_payload]
        parts += [c.payload for c in self._children]
        if self._footer_payload:
            parts.append(self._footer_payload)
        return "\n".join(p for p in parts if p)

    def compose(self) -> ComposeResult:
        yield Static(self._header, classes="sa-header")
        yield Static(self._body_renderable(), classes="sa-body")

    def _body_renderable(self) -> RenderableType:
        if self.collapsed:
            return Text("")
        rends: list[RenderableType] = [c.renderable for c in self._children]
        if self._footer is not None:
            rends.append(self._footer)
        return Group(*rends) if rends else Text("")

    def _refresh(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(".sa-header", Static).update(self._header)
        with contextlib.suppress(Exception):
            self.query_one(".sa-body", Static).update(self._body_renderable())

    def on_click(self, event: Click) -> None:
        self.toggle()


def _payload_for_event(ev) -> str:
    """Plain-text clipboard payload for a non-streaming Event."""
    from aegis.events import Result, ToolResult, ToolUse
    if isinstance(ev, ToolUse):
        return (f"{ev.name}({ev.summary})" if ev.summary
                else f"{ev.name}()")
    if isinstance(ev, ToolResult):
        return ev.text or ""
    if isinstance(ev, Result):
        secs = (ev.duration_ms or 0) / 1000
        return f"done in {secs:.1f}s"
    # AssistantText / Thinking are streamed elsewhere; other events
    # already returned None from render_event.
    return getattr(ev, "text", "") or repr(ev)


class PaneStateChanged(Message):
    def __init__(self, pane: "ConversationPane",
                 finished: bool) -> None:
        self.pane = pane
        self.finished = finished
        super().__init__()


class ConversationPane(Widget):
    # Class-level defaults for the streaming-repaint state: unit tests
    # build a pane with __new__ (no __init__, no Textual boot) and still
    # reach _flush_streaming through clear_transcript. Same exposure the
    # tick path had in 0.28.0.
    _repaint_pending = False
    _window_end = 0
    _restoring_tail = False
    _last_repaint_at = 0.0
    _repaint_timer = None

    DEFAULT_CSS = """
    ConversationPane { layout: vertical; height: 1fr;
                       background: $background; }
    ConversationPane #transcript-row { height: 1fr; }
    ConversationPane #transcript { height: 1fr; width: 1fr;
                                   background: $background;
                                   padding: 1 4; scrollbar-size: 0 0; }
    ConversationPane StatusBar { height: 1; background: $panel;
                                 color: $foreground; padding: 0 2; }
    ConversationPane GrowingInput { height: 3; background: $surface;
                             color: $foreground; padding: 0 2;
                             border: none;
                             margin-top: 1;
                             scrollbar-size: 0 0; }
    /* Idle (default): vivid outline — a live agent that acts on your
       message immediately. */
    ConversationPane GrowingInput,
    ConversationPane GrowingInput:focus { border: none;
                             border-top: solid $success;
                             border-bottom: solid $success; }
    /* Working: subdued outline — the agent is mid-turn and your message
       queues behind it. */
    ConversationPane.working GrowingInput,
    ConversationPane.working GrowingInput:focus {
                             border-top: solid $foreground 30%;
                             border-bottom: solid $foreground 30%; }
    /* Shell escape: the input starts with `!` — it runs as a local shell
       command, not a message to the agent. Magenta flags the difference.
       After .working so it wins when you type `!` mid-turn. */
    ConversationPane.shell-escape GrowingInput,
    ConversationPane.shell-escape GrowingInput:focus {
                             color: #C77DBB;
                             border-top: solid #C77DBB;
                             border-bottom: solid #C77DBB; }
    /* Slash command: the input starts with `/` — aegis runs it directly.
       Bright blue, distinct from magenta shell / green message. */
    ConversationPane.slash-command GrowingInput,
    ConversationPane.slash-command GrowingInput:focus {
                             color: #4DA6FF;
                             border-top: solid #4DA6FF;
                             border-bottom: solid #4DA6FF; }
    /* Voice recording overrides all. */
    ConversationPane.recording GrowingInput,
    ConversationPane.recording GrowingInput:focus {
                             border-top: solid $warning;
                             border-bottom: solid $warning; }
    """

    @property
    def _host(self) -> str:
        """The machine this pane's harness runs on. Drives every local
        file affordance: off-host, a path in the transcript names a file
        that is not here."""
        return getattr(self, "_place", None).host \
            if getattr(self, "_place", None) else "local"

    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_slug: str, handle: str, palette,
                 *, digest=None, monitor_manager=None,
                 state_dir_path: Path | None = None,
                 replay: EventReplay | None = None,
                 on_first_user_message: Callable[[str], None] | None = None,
                 core=None, log_id: str | None = None,
                 place=None) -> None:
        super().__init__(id=f"pane-{handle}")
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self._digest = digest
        self._monitor_manager = monitor_manager
        self._created_at: str = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.unseen = False
        # ``core`` allows remote mode to inject a RemotePaneCore directly,
        # bypassing the AgentSession wrapping that requires a real HarnessSession.
        if core is not None:
            self._core = core
        else:
            self._core = AgentSession(session, agent, agent_slug, handle,
                                      log_id=log_id, place=place)
        self._core.add_event_observer(self._on_core_event)
        # RemotePaneCore has no place of its own — a --remote session's
        # harness lives in the serve it is attached to, not here.
        if not hasattr(self._core, "place"):
            from aegis.hosts.models import Place
            self._core.place = place or Place("local", ".")
        self._place = self._core.place
        self._core.add_state_observer(self._on_core_state)
        self._core.add_inbox_observer(self._on_core_inbox)
        self._core.add_dispatch_observer(self._on_core_dispatch)
        # Single primary slot (no add_loop_observer — one frontend owns the
        # chip). Harmless on a RemotePaneCore, which has no loop of its own.
        self._core.on_loop = self._on_loop_change
        # RemotePaneCore has no log of its own; fall back to the handle so
        # the attribute always answers.
        self.log_id: str = getattr(self._core, "log_id", None) or handle
        if state_dir_path is not None:
            # Keyed on the session's immutable log id, never the handle:
            # handles are recycled out of a finite pool, so two unrelated
            # sessions sharing one would append into the same file.
            self._core.add_event_observer(
                make_session_log_observer(state_dir_path, self.log_id))
        self._replay = replay
        # Rebuild plan state before the pane paints anything, so a resumed
        # tab shows its task list immediately instead of staying blank
        # until the agent happens to call TaskList. Every resume path
        # (boot, reopen-from-history, /reconnect) funnels through here.
        # Guarded: a RemotePaneCore has no tracker to rehydrate.
        if replay is not None and hasattr(self._core, "rehydrate_plan"):
            self._core.rehydrate_plan(replay.events, replay.stamps)
        # Same reasoning for the title, and it matters more than the label:
        # title_source is what stops an agent overwriting what Alex typed,
        # so a resumed session that forgot it would hand his authority back
        # to the agents on every restart. Last non-empty wins, exactly as
        # the history fold reads it (state/history.py).
        if replay is not None and hasattr(self._core, "title"):
            for ev in replay.events:
                if isinstance(ev, SessionMeta) and ev.title:
                    self._core.title = ev.title
                    self._core.title_source = ev.title_source
        # Fires once, with the text of the first user-initiated turn — the
        # hook AegisApp uses to write the session's Ctrl+H history header
        # lazily (so its preview is populated). Both delivery paths
        # (_submit and the text-box deliver) route through
        # _record_first_user_message.
        self._on_first_user_message = on_first_user_message
        self._first_msg_recorded = False
        # Streaming aggregation state: while inside a run of
        # AssistantText (or AssistantThinking) events we accumulate
        # into one CopyableBlock and update it in place.
        self._streaming_block: CopyableBlock | None = None
        self._streaming_kind: str | None = None     # "text" | "thinking"
        self._streaming_text: str = ""
        self._streaming_thinking_est: int = 0
        self._thinking_started_at: float | None = None
        # Windowing: every rendered block lives here; only
        # _history[_window_start:] is mounted. _streaming_history_idx
        # points at the record currently being mutated by streaming
        # aggregation (None when no stream is in flight).
        self._history: list[BlockRecord] = []
        self._window_start: int = 0
        # Exclusive end of the mounted slice. The window used to be a
        # suffix — always ending at the newest record — which is why
        # reading back through a thread while an agent worked mounted
        # every new block forever (audit finding 5). With both edges,
        # eviction can take from whichever end is furthest from the
        # viewport, so it never fights _load_older for the rows you are
        # actually looking at.
        self._window_end: int = 0
        self._streaming_history_idx: int | None = None
        # Plan owner key (None = top level, else parent_tool_use_id) →
        # (block, history index). One block per plan, mutated in place.
        self._plan_blocks: dict = {}
        # tool_call_id → history index of that tool call's ToolUse block, so
        # its ToolResult folds into the *same* block instead of appending a
        # trailing one. Parallel tool calls emit all uses first, then all
        # results — folding by id keeps each result under its own call.
        self._tool_use_idx: dict[str, int] = {}
        # Per-tool-call live track (spinner + timer + expandable args).
        # tool_call_id → _ToolTrack; a single set_interval ticks all
        # not-yet-done tracks once a second.
        self._tools: dict[str, _ToolTrack] = {}
        self._tool_timer = None
        self._spin_frame = 0
        # The one running deferred command (/btw, @peer), or None. Shares
        # the tool ticker: a deferred placeholder and a tool call spin off
        # the same 10 Hz timer.
        self._deferred: _DeferredTrack | None = None
        # Task tool_call_id → its SubagentBox. Events tagged with a known
        # parent_tool_use_id route into the matching box instead of the
        # transcript; the Task's own tool_result closes it.
        self._subagent_boxes: dict[str, SubagentBox] = {}
        self._subagent_counts: dict[str, int] = {}
        self._subagent_summary: dict[str, str] = {}
        # Explicit list of currently-mounted CopyableBlocks in DOM order.
        # Source of truth for eviction — Textual's .remove() defers until
        # the next layout tick, so t.query(CopyableBlock) returns stale
        # results for tight loops of mount+evict.
        self._mounted_blocks: list[CopyableBlock] = []
        self._stick_to_bottom: bool = True
        self._loading_older: bool = False
        self._load_timer = None
        # Widgets that are composed once and never replaced. Resolved lazily
        # through _bar() / _working_indicator(), which are on the per-event
        # path — a deep query there walks the whole transcript.
        self._status_bar: "StatusBar | None" = None
        self._indicator: "WorkingIndicator | None" = None
        # Replay is painted on first show, not on mount (see on_mount).
        self._replayed: bool = False
        # Set when a delta landed while this tab was hidden, or while the
        # streaming repaint was inside its frame window (see
        # _stream_append). on_show / the deferred flush reconcile the
        # widget with the record.
        self._repaint_pending: bool = False
        self._last_repaint_at: float = 0.0
        self._repaint_timer = None
        # Newest turn terminator, so its "x ago" can be kept current while
        # you look at the tab. Only the newest carries an age: a frozen
        # "2s ago" on an hour-old turn is worse than no age at all.
        self._last_result: _ResultBlock | None = None

    @property
    def state(self) -> AgentState:
        return self._core.state

    @property
    def _session(self) -> HarnessSession:
        return self._core._session

    def set_palette(self, palette) -> None:
        self._palette = palette
        for w in self.query(QueueStrip):
            w.set_palette(palette)
        for w in self.query(MonitorStrip):
            w.set_palette(palette)
        for w in self.query(PendingStrip):
            w.set_palette(palette)

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="transcript-row"):
                yield VerticalScroll(id="transcript")
                yield PlanDock(self._palette, id="plan-dock")
            if self._digest is not None:
                yield QueueStrip(self._digest, self._palette)
            if self._monitor_manager is not None:
                yield MonitorStrip(self._monitor_manager, self._palette,
                                   handle_of=lambda: self.handle)
            # In remote mode, agent may be None; fall back to empty strings.
            _model = getattr(self._agent, "model", "") if self._agent else ""
            _eff_raw = getattr(self._agent, "effort", "") if self._agent else ""
            _eff = getattr(_eff_raw, "value", _eff_raw)  # Effort enum → str
            yield PlanStrip(self._palette, id="plan-strip")
            yield StatusBar(_model, _eff, self._palette)
            yield CommandPalette(self._palette)
            yield PendingStrip(self._palette)
            yield GrowingInput(placeholder="type a message…")

    async def on_mount(self) -> None:
        self.query_one(StatusBar).set_state(AgentState.ready)
        # Boot mounts every resumed tab hidden and only shows one, so a pane
        # you may never look at shouldn't pay to paint itself. Deferred to
        # on_show, which makes boot cost O(1) in tab count instead of the
        # sum of every tab's replay.
        if self.display:
            self._mount_replay()
        self.refresh_metrics()
        # Paint a rehydrated plan once at mount. Every other call site is
        # event-driven (an AgentPlan arriving, or turn state changing), and
        # a resumed tab may sit for a long time before either fires — which
        # is exactly how a restored plan still showed a blank strip.
        self._refresh_plan_surfaces()
        t = self._transcript()
        self.watch(t, "scroll_y", self._on_scroll_y)
        self.query_one(GrowingInput).key_interceptor = self._palette_key

    def on_show(self) -> None:
        """Tab brought forward: paint a deferred replay if this is the first
        look, resume the 10 Hz visual timers that were frozen while hidden,
        and snap to the tail if the user was following."""
        self._mount_replay()          # no-op once it has run
        self._catch_up_streaming_block()
        ind = self._working_indicator()
        if ind is not None:
            ind.set_animating(True)
        if self._any_tool_running():
            self._ensure_tool_timer()
        # Coming back to a tab is exactly when "how long ago did this end"
        # matters, and a hidden pane's age went stale while it sat there.
        self.refresh_result_age()
        if self._stick_to_bottom:
            self._transcript().scroll_end(animate=False)

    def _due_for_repaint(self) -> bool:
        return (time.monotonic() - self._last_repaint_at) >= STREAM_REPAINT_S

    def _paint_streaming(self, renderable, payload: str | None = None) -> None:
        """Push the current stream render into the widget and follow it
        down. Both halves cost a reflow, so they move together — a delta
        we did not paint is a delta we do not need to scroll to."""
        self._last_repaint_at = time.monotonic()
        self._repaint_pending = False
        if self._streaming_block is not None:
            self._streaming_block.update_content(
                renderable,
                self._streaming_text if payload is None else payload)
        # Explicit (not reliant on Textual's scroll anchor, which drifts in
        # a live terminal); gated on stickiness so a user scrolled up to
        # read is never yanked down.
        if self._stick_to_bottom:
            self._transcript().scroll_end(animate=False)

    def _arm_repaint_flush(self) -> None:
        """Guarantee a skipped delta still lands, even if it was the last
        one before the stream went quiet."""
        if self._repaint_timer is not None or not self.display:
            return
        with contextlib.suppress(Exception):
            self._repaint_timer = self.set_timer(
                STREAM_REPAINT_S, self._flush_repaint)

    def _flush_repaint(self) -> None:
        self._repaint_timer = None
        self._catch_up_streaming_block()

    def _catch_up_streaming_block(self) -> None:
        """Reconcile the streaming widget with its record — after the tab
        was hidden through part of a turn, or after the repaint throttle
        skipped the newest delta."""
        if not self._repaint_pending:
            return
        idx = self._streaming_history_idx
        if (self._streaming_block is None or idx is None
                or not (0 <= idx < len(self._history))):
            self._repaint_pending = False
            return
        rec = self._history[idx]
        with contextlib.suppress(Exception):
            self._paint_streaming(rec.renderable, rec.payload)

    def on_hide(self) -> None:
        """Tab sent to the background: freeze its cosmetic spinner timers so
        they stop taxing the shared message pump. State/history untouched."""
        ind = self._working_indicator()
        if ind is not None:
            ind.set_animating(False)
        self._stop_tool_timer()

    def _mount_replay(self) -> None:
        """Paint prior events onto the transcript on resume.

        Builds the full ``_history`` cheaply — plain dataclass records off
        the coalesced event stream, no widgets — then mounts only the last
        ``REPLAY_TAIL`` blocks. A long resumed session paints instantly
        instead of mounting (and immediately evicting) hundreds of widgets.
        Older blocks are reconstructed on demand by ``_load_older`` when
        Alex scrolls up."""
        if self._replay is None or self._replayed:
            return
        self._replayed = True
        records: list[BlockRecord] = []
        use_idx: dict[str, int] = {}   # tool_call_id → record index
        box_idx: dict[str, int] = {}   # Task tool_call_id → box record index
        open_box: dict[str, int] = {}  # still-open Task tool_call_id → index

        def _fold_into(idx: int, ev) -> None:
            if renders_to_nothing(ev):
                return
            rec = records[idx]
            rec.events.append(ev)          # rendered if the block is mounted
            rec.payload = f"{rec.payload}\n{_payload_for_event(ev)}"

        for ev in fold_plan_events(coalesce_chunks(self._replay.events)):
            # Subagent child → fold flat into its Task box record.
            parent = getattr(ev, "parent_tool_use_id", None)
            if parent is not None and parent in box_idx:
                _fold_into(box_idx[parent], ev)
                continue
            # Task's own result closes its box (footer).
            if isinstance(ev, ToolResult) and ev.tool_call_id in open_box:
                _fold_into(open_box.pop(ev.tool_call_id), ev)
                continue
            # Fold a ToolResult into its matching ToolUse record so the pair
            # renders as one block — mirrors the live _fold_tool_result path.
            if isinstance(ev, ToolResult) and ev.tool_call_id in use_idx:
                _fold_into(use_idx[ev.tool_call_id], ev)
                continue
            if renders_to_nothing(ev):
                continue
            records.append(BlockRecord(
                None, _payload_for_event(ev), False, events=[ev],
                file_target=(file_target(ev.name, ev.raw_input, ev.locations,
                                         host=self._host)
                             if isinstance(ev, ToolUse) else None)))
            if (isinstance(ev, ToolUse) and ev.name in _SUBAGENT_TOOLS
                    and ev.tool_call_id):
                box_idx[ev.tool_call_id] = len(records) - 1
                open_box[ev.tool_call_id] = len(records) - 1
            elif isinstance(ev, ToolUse) and ev.tool_call_id:
                use_idx[ev.tool_call_id] = len(records) - 1
        if self._replay.interrupted:
            records.append(BlockRecord(
                Text("⚠ interrupted", style="yellow"), "⚠ interrupted", False))

        self._history = records
        self._window_start = max(0, len(records) - REPLAY_TAIL)
        self._window_end = len(records)
        t = self._transcript()
        for rec in records[self._window_start:]:
            block = CopyableBlock(rec.materialize(self._palette), rec.payload,
                                  tight=rec.tight,
                                  file_target=rec.file_target)
            t.mount(block)
            self._mounted_blocks.append(block)
        t.scroll_end(animate=False)

    def _bar(self) -> "StatusBar | None":
        """The pane's StatusBar, cached — or None before it mounts.

        `self.query(StatusBar)` is a deep, uncached CSS walk over every
        descendant, and a pane's descendants are its transcript blocks. This
        sits on the per-event path, so looking up a widget that never moves
        made handling one streamed token cost 3.3 ms on a fresh tab and 36 ms
        at the eviction cap. The bar is composed once and never replaced;
        `is_attached` covers teardown, when the answer becomes None again.
        """
        bar = self._status_bar
        if bar is not None and bar.is_attached:
            return bar
        from textual.css.query import NoMatches
        try:
            self._status_bar = self.query_one(StatusBar)
        except NoMatches:
            # Core observers can fire before compose finishes mounting it.
            self._status_bar = None
        return self._status_bar

    def refresh_metrics(self) -> None:
        bar = self._bar()
        if bar is not None:
            bar.set_metrics(
                self._core.metrics.render_tiers(time.monotonic()))

    def set_system(self, text) -> None:
        """Push the system-stats segment (sampled app-side) to the StatusBar."""
        bar = self._bar()
        if bar is not None:
            bar.set_system(text)

    def set_quota(self, tiers) -> None:
        """Push the quota segment (sampled app-side) to the StatusBar."""
        bar = self._bar()
        if bar is not None:
            bar.set_quota(tiers)

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _on_scroll_y(self, _value: float) -> None:
        t = self._transcript()
        self._stick_to_bottom = (
            (t.max_scroll_y - t.scroll_y) <= STICKY_EPS)
        if (self._stick_to_bottom and not self._restoring_tail
                and self._window_end < len(self._history)):
            # Scrolled back down to a window whose tail was dropped while
            # we were reading above. Without this the bottom of the
            # transcript silently shows stale content. Debounced, and
            # guarded because jump_to_end scrolls (and so re-enters here).
            self._restoring_tail = True

            def _restore_tail() -> None:
                try:
                    self.jump_to_end()
                finally:
                    self._restoring_tail = False

            self.set_timer(DEBOUNCE_S, _restore_tail)
            return
        near_top = t.scroll_y <= LOAD_MORE_EPS
        if near_top and self._window_start > 0 and not self._loading_older:
            if self._load_timer is not None:
                with contextlib.suppress(Exception):
                    self._load_timer.stop()
            self._load_timer = self.set_timer(
                DEBOUNCE_S, self._load_older)

    def _load_older(self) -> None:
        if self._loading_older or self._window_start == 0:
            return
        self._loading_older = True
        try:
            t = self._transcript()
            new_start = max(0, self._window_start - LOAD_BATCH)
            anchor = self._mounted_blocks[0] if self._mounted_blocks else None
            anchor_y_before = (
                (anchor.region.y - t.region.y) if anchor is not None else 0)
            new_blocks: list[CopyableBlock] = [
                CopyableBlock(rec.materialize(self._palette), rec.payload,
                              tight=rec.tight, tool_call_id=rec.tool_call_id,
                              file_target=rec.file_target)
                for rec in self._history[new_start : self._window_start]
            ]
            # One batched mount: Textual lays the parent out once per mount
            # call, so mounting LOAD_BATCH blocks one at a time paid
            # LOAD_BATCH full-screen reflows per scroll-up.
            if new_blocks:
                if anchor is not None:
                    t.mount(*new_blocks, before=anchor)
                else:
                    t.mount(*new_blocks)
            # Prepend new blocks to the explicit mounted list (DOM order).
            self._mounted_blocks[:0] = new_blocks
            self._window_start = new_start
            self._bound_window()

            def _restore() -> None:
                if anchor is not None:
                    anchor_y_after = anchor.region.y - t.region.y
                    delta = anchor_y_after - anchor_y_before
                    if delta:
                        t.scroll_to(
                            y=t.scroll_y + delta, animate=False)
                self._loading_older = False

            self.call_after_refresh(_restore)
        except Exception:
            self._loading_older = False
            raise

    def _working_indicator(self) -> WorkingIndicator | None:
        # Cached for the same reason as _bar(): this is called on every
        # mounted block, and the deep query walked the whole transcript.
        ind = self._indicator
        if ind is not None and ind.is_attached:
            return ind
        from textual.css.query import NoMatches
        try:
            self._indicator = self.query_one(WorkingIndicator)
        except NoMatches:
            self._indicator = None
        return self._indicator

    def _apply_command_effect(self, effect: dict) -> None:
        """Apply a slash-command frontend effect (theme switch, transcript
        clear). Unknown kinds are ignored (forward-compatible)."""
        kind = effect.get("kind")
        if kind == "theme":
            self.app.theme = effect["name"]
        elif kind == "tasks":
            self.toggle_task_dock()
        elif kind == "clear":
            from rich.text import Text

            from aegis.tui.metrics import _fmt_tokens
            for b in self._mounted_blocks:
                with contextlib.suppress(Exception):
                    b.remove()
            self._mounted_blocks.clear()
            self._history.clear()
            self._window_start = 0
            self._window_end = 0
            ctx_tokens = self._core.metrics.last_true_input
            marker = (f"──── transcript cleared · {_fmt_tokens(ctx_tokens)} "
                      f"context tokens still in play ────")
            self._mount_block(
                Text(marker, style=self._palette.muted, justify="center"),
                marker)

    def _put_block(self, renderable: RenderableType, payload: str,
                   *, at_idx: int | None = None) -> None:
        """Mount a new block, or rewrite the block at ``at_idx`` in place.

        The deferred path needs the second: its placeholder was mounted
        when the command started, and the result must land *there* rather
        than at the tail — so a side note stays where you asked it while
        the agent's output streams past underneath. Mirrors what
        ``_render_tool_block`` does when a tool call's result arrives.
        """
        if at_idx is None:
            self._mount_block(renderable, payload)
            return
        rec = self._history[at_idx]
        rec.renderable = renderable
        rec.payload = payload
        pos = at_idx - self._window_start
        if 0 <= pos < len(self._mounted_blocks):
            self._mounted_blocks[pos].update_content(renderable, payload)
            if self._stick_to_bottom:
                self._transcript().scroll_end(animate=False)

    def _apply_command_result(self, result, width: int,
                              *, at_idx: int | None = None) -> str | None:
        """Render one command result into the transcript.

        Extracted from ``on_growing_input_submitted`` because deferring
        splits the call into two sites — inline and worker-completion —
        and a duplicated chain would drift. The first casualty would be
        ``peer_answer``, which has to keep working on both paths while
        ``@peer`` flips ``deferred`` beneath it.

        Returns the text to deliver to the agent for a ``deliver`` effect,
        else None. ``deliver`` is the one branch that cannot be deferred,
        since it produces a message to send rather than a block to mount;
        prompt commands are not deferred, so this costs nothing.
        """
        eff = result.effect or {}
        kind = eff.get("kind")
        if kind == "deliver":
            # Prompt command: its expansion is delivered to the agent as a
            # normal user message (rendered as a user line by
            # _on_core_dispatch), not a command-result block.
            return eff["text"]
        if kind == "side_note":
            # A /btw answer gets its own treatment — it is neither a user
            # line nor agent output. It lands in _history (so scrolling
            # keeps it) and is never appended to the session log, so it
            # does not survive a reload and never enters the window a
            # later /btw assembles.
            from aegis.btw import SideNote
            from aegis.render import render_side_note
            note = SideNote(**eff["note"])
            self._flush_streaming()
            self._put_block(render_side_note(note, self._palette),
                            f"btw: {note.answer}\n{note.footer}".strip(),
                            at_idx=at_idx)
            return None
        if kind == "peer_answer":
            # An @peer answer is transient *here* and real *there*: it
            # lands in this pane's _history and is never appended to this
            # session's log, so the agent you are sitting with neither
            # sees it nor pays for it. The same answer is a genuine turn
            # in the peer's own transcript.
            from aegis.peer import PeerAnswer
            from aegis.render import render_peer_answer
            answer = PeerAnswer(**eff["answer"])
            self._flush_streaming()
            self._put_block(
                render_peer_answer(answer, self._palette),
                f"@{answer.target}: {answer.answer}\n"
                f"{answer.footer}".strip(), at_idx=at_idx)
            return None
        from aegis.render import render_command_block
        self._flush_streaming()
        self._put_block(render_command_block(result, self._palette, width),
                        f"{result.title}\n{result.body}".strip(),
                        at_idx=at_idx)
        if result.effect:
            self._apply_command_effect(result.effect)
        return None

    def _remote_path_for(self, ev) -> str | None:
        """The host-qualified path a remote tool call names, if any.

        ``file_target`` deliberately returns None off-host, so this is
        what the block offers instead: ``vps:/srv/app/x.py``, which says
        both which file and — decisively — which machine.
        """
        if self._host == "local":
            return None
        path = (getattr(ev, "raw_input", None) or {}).get("file_path")
        return self._place.qualify(str(path)) if path else None

    def toggle_task_dock(self) -> bool:
        """Open/close the task dock. Bound to F3 and to `/tasks`."""
        try:
            dock = self.query_one("#plan-dock", PlanDock)
        except Exception:
            return False
        self._refresh_plan_surfaces()
        return dock.toggle()

    def _plan_key(self, ev) -> str | None:
        return getattr(ev, "parent_tool_use_id", None)

    def _replace_plan_block(self, ev) -> bool:
        """Update this plan's existing transcript block in place. Returns
        False when there is none yet, so the caller mounts one."""
        found = self._plan_blocks.get(self._plan_key(ev))
        if found is None:
            return False
        block, idx = found
        renderable = render_event(ev, self._palette)
        if renderable is None:
            return True
        payload = _payload_for_event(ev)
        if 0 <= idx < len(self._history):
            rec = self._history[idx]
            rec.renderable, rec.payload = renderable, payload
        try:
            block.update_content(renderable, payload)
        except Exception:
            # Block pruned by the scroll window; the history record above
            # is the source of truth and will re-render on remount.
            self._plan_blocks.pop(self._plan_key(ev), None)
            return False
        return True

    def _mount_block(self, renderable: RenderableType,
                     text_payload: str,
                     *, tight: bool = False,
                     tool_call_id: str | None = None,
                     file_target: FileTarget | None = None,
                     remote_path: str | None = None) -> CopyableBlock:
        self._history.append(
            BlockRecord(renderable, text_payload, tight, tool_call_id,
                        file_target=file_target))
        block = CopyableBlock(renderable, text_payload, tight=tight,
                              tool_call_id=tool_call_id,
                              file_target=file_target,
                              remote_path=remote_path,
                              host=self._host)
        t = self._transcript()
        if self._window_end < len(self._history) - 1:
            # The tail is already truncated (we are scrolled up and the
            # window stopped following the newest content). Record it and
            # leave it unmounted; jump_to_end/_on_scroll_y bring it back.
            return block
        ind = self._working_indicator()
        if ind is not None and ind.parent is t:
            # Keep the indicator pinned to the END of the transcript by
            # inserting new blocks BEFORE it. As the agent streams and
            # mounts new ToolUse / ToolResult / etc. blocks, the
            # indicator stays right under the latest content.
            t.mount(block, before=ind)
        else:
            t.mount(block)
        self._mounted_blocks.append(block)
        self._window_end = len(self._history)
        if self._stick_to_bottom:
            t.scroll_end(animate=False)
        self._bound_window()
        return block

    def _evict_top(self, n: int) -> None:
        """Unmount the first n mounted CopyableBlocks and advance
        _window_start. Safe to call only while _stick_to_bottom is True:
        the user is at the tail, so removing widgets above the viewport
        doesn't disturb them.

        One batched prune, not n of them: Textual refreshes the parent with
        layout=True once per prune, so removing EVICT_BATCH blocks
        individually was a burst of EVICT_BATCH full-screen reflows every
        time the window filled."""
        doomed = self._mounted_blocks[:n]
        if doomed:
            with contextlib.suppress(Exception):
                self._transcript().remove_children(doomed)
        del self._mounted_blocks[:n]
        self._window_start += n

    def _evict_bottom(self, n: int) -> None:
        """Unmount the last n mounted blocks and pull _window_end back.

        The counterpart to _evict_top, for when the viewport is near the
        top: the newest blocks are the ones off-screen, so they are the
        ones to drop. Evicting the top there would take the rows being
        read and be immediately undone by _load_older."""
        if n <= 0:
            return
        doomed = self._mounted_blocks[-n:]
        if not doomed:
            return
        if self._streaming_block in doomed:
            # The live block is going away; the record stays authoritative
            # and jump_to_end re-links it when the tail comes back.
            self._streaming_block = None
            self._repaint_pending = True
        with contextlib.suppress(Exception):
            self._transcript().remove_children(doomed)
        del self._mounted_blocks[-n:]
        self._window_end -= len(doomed)

    def _bound_window(self) -> None:
        """Keep the mounted slice within N_MAX, dropping from whichever
        edge is furthest from the viewport.

        Bounded regardless of stickiness — that gate is exactly what let
        the window grow without limit while scrolled up (finding 5)."""
        if self._window_end - self._window_start <= N_MAX:
            return
        t = self._transcript()
        # Nearer the top than the bottom => the far edge is the newest.
        near_top = t.scroll_y <= (t.max_scroll_y / 2)
        if near_top and not self._stick_to_bottom:
            self._evict_bottom(EVICT_BATCH)
        else:
            self._evict_top(EVICT_BATCH)

    def jump_to_end(self) -> None:
        """Return to the newest content, remounting the tail if the window
        stopped following it."""
        if self._window_end < len(self._history):
            t = self._transcript()
            start = max(0, len(self._history) - REPLAY_TAIL)
            for b in list(self._mounted_blocks):
                with contextlib.suppress(Exception):
                    b.remove()
            self._mounted_blocks.clear()
            new_blocks = [
                CopyableBlock(rec.materialize(self._palette), rec.payload,
                              tight=rec.tight, tool_call_id=rec.tool_call_id,
                              file_target=rec.file_target)
                for rec in self._history[start:]
            ]
            ind = self._working_indicator()
            if new_blocks:
                if ind is not None and ind.parent is t:
                    t.mount(*new_blocks, before=ind)
                else:
                    t.mount(*new_blocks)
            self._mounted_blocks[:] = new_blocks
            self._window_start = start
            self._window_end = len(self._history)
            # Re-link the live block so streaming keeps painting.
            idx = self._streaming_history_idx
            if idx is not None and start <= idx < self._window_end:
                self._streaming_block = self._mounted_blocks[idx - start]
        self._stick_to_bottom = True
        self._transcript().scroll_end(animate=False)

    def _start_indicator(self) -> None:
        """Ensure a live, animating WorkingIndicator at the bottom of the
        transcript. Idempotent — (re)starts a lingering or frozen one so a
        self-woken or chained turn always shows a fresh spinner."""
        ind = self._working_indicator()
        if ind is None:
            ind = WorkingIndicator(self._palette)
            self._transcript().mount(ind)
            self._indicator = ind
        # Only animate while this tab is visible; a background pane freezes
        # its spinner (on_show resumes it) to spare the shared message pump.
        ind.start(animate=self.display)
        self._transcript().scroll_end(animate=False)

    def _stop_indicator(self) -> None:
        """Stop + remove the WorkingIndicator if mounted."""
        ind = self._working_indicator()
        if ind is None:
            return
        with contextlib.suppress(Exception):
            ind.stop()
        with contextlib.suppress(Exception):
            ind.remove()
        self._indicator = None

    def _transcript_blocks(self) -> list[CopyableBlock]:
        return list(self.query(CopyableBlock))

    def _transcript_has(self, needle: str) -> bool:
        return any(needle in b.text_payload()
                   for b in self._transcript_blocks())

    def focus_input(self) -> None:
        self.query_one(GrowingInput).focus()

    def input_widget(self) -> "GrowingInput":
        return self.query_one(GrowingInput)

    def cancel_deferred_if_running(self) -> bool:
        """Esc handler: cancel a running deferred command and report we
        consumed the key. Nothing running → return False so the app falls
        through to clearing the input, then interrupting the turn.

        The block becomes a tombstone rather than disappearing: ESC
        silently deleting something you can see reads as a glitch, and
        this block is the only record that you spent anything at all.

        The tombstone carries the *command's* own cancel note, because
        the truth differs per command. "cancelled" is honest for `/btw`,
        which never touched a harness session. For `@peer` it is a lie —
        the peer already took the turn and finishes into its own
        transcript whether or not anyone is listening.
        """
        track = self._deferred
        if track is None or track.done:
            return False
        track.done = True
        track.elapsed = time.monotonic() - track.start
        # Cleared before cancelling the worker so the in-flight
        # _run_deferred sees `self._deferred is not track` and drops its
        # result, whichever way the cancellation lands.
        self._deferred = None
        if track.worker is not None:
            with contextlib.suppress(Exception):
                track.worker.cancel()
        self._render_deferred_block(track, cancelled=True)
        if not self._any_spinner_running():
            self._stop_tool_timer()
        return True

    def clear_input_if_present(self) -> bool:
        """Esc handler: clear a non-empty input and report we consumed the
        key. Empty input → no-op, return False so the app interrupts."""
        inp = self.query_one(GrowingInput)
        if inp.value.strip():
            inp.value = ""
            return True
        return False

    def set_recording(self, on: bool) -> None:
        self.set_class(on, "recording")

    def on_text_area_changed(self, _event) -> None:
        # Flag special input prefixes with a distinct outline colour so they
        # read as different from a plain message: `!` shell-escape → magenta,
        # `/` slash command → bright blue. Idempotent; both clear when the
        # input is emptied (on submit) or no longer starts with the prefix.
        value = self.query_one(GrowingInput).value
        self.set_class(value.startswith("!"), "shell-escape")
        # `@handle …` is sugar classify_input rewrites into `/peer handle …`,
        # so it is a command in every way that matters and earns the same
        # type-time signals: the command outline, and a palette listing live
        # handles. `@@` is the literal-@ escape and addresses nobody, so it
        # gets neither — and `complete()` already returns nothing for it.
        #
        # There are four `startswith("/")` gates in this widget. Widening
        # only the submit one (see on_growing_input_submitted) left `@` a
        # command you could send but could not discover.
        addressing = value.startswith("@") and not value.startswith("@@")
        self.set_class(value.startswith("/") or addressing, "slash-command")
        pal = self.query_one(CommandPalette)
        if value.startswith("/") or addressing:
            from aegis.commands import complete
            pal.update(complete(value, self.app))
        else:
            pal.hide()

    def _palette_key(self, event) -> bool:
        """Key hook the GrowingInput consults first: while the palette is open,
        Up/Down move the highlight, Tab/Enter accept, Esc dismisses."""
        pal = self.query_one(CommandPalette)
        if not pal.display:
            return False
        if event.key in ("up", "down"):
            pal.move(-1 if event.key == "up" else 1)
            return True
        if event.key in ("tab", "enter"):
            choice = pal.current()
            if choice is None:
                return False
            self._accept_completion(choice)
            return True
        if event.key == "escape":
            pal.hide()
            return True
        return False

    def _accept_completion(self, choice) -> None:
        inp = self.query_one(GrowingInput)
        value = inp.value
        if value.startswith("/") and " " not in value:
            new = choice.insert                      # completing the verb
        else:
            head = value.rsplit(" ", 1)[0] if " " in value else ""
            new = (head + " " if head else "") + choice.insert
        inp.value = new
        inp.move_cursor(inp.document.end)
        from aegis.commands import complete
        self.query_one(CommandPalette).update(complete(new, self.app))

    async def on_growing_input_submitted(self,
                                  event: GrowingInput.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if not text:
            return
        inp = self.query_one(GrowingInput)
        inp.value = ""
        # `!command` shell escape: run it locally in the project root and
        # inject the output as the message the agent sees. A bare `!` is a
        # no-op.
        if text.startswith("!"):
            command = text[1:].strip()
            if not command:
                return
            from aegis.tui.shell_escape import run_shell_escape
            text = await run_shell_escape(command, self._core.project_root)
        elif text.startswith(("/", "@")):
            # Slash family: `/cmd` is a command aegis executes directly and
            # renders in the transcript (never delivered to the agent); `//x`
            # is an escape that delivers a literal `/x` message.
            #
            # `@` is in the gate because classify_input rewrites `@beta hi`
            # into `/peer beta hi`. This tuple has to travel with the
            # classify_input call wherever it moves: without it an `@` line
            # falls through and is delivered to the agent you are sitting
            # with as literal text — worse than not having the feature,
            # because it looks like it worked. The web seam has no gate
            # (wssession.py calls classify_input on every line), so a
            # regression here is TUI-only and silent.
            from aegis.commands import (
                CommandContext, classify_input, dispatch)
            kind, payload = classify_input(text)
            if kind == "command":
                width = self._transcript().size.width or 80
                # A deferred command must not be awaited here: this is a
                # Textual message handler, and holding it holds the pane's
                # whole message pump — no working indicator, no tool
                # spinners, no input, for the 12-17s /btw takes or the up
                # to 300s @peer can.
                #
                # `payload`, never `text`: resolve_deferred parses a verb,
                # and `@beta hi` has none until classify_input has
                # rewritten it to `/peer beta hi`. Resolving the raw line
                # returns None for every @ spelling and drops it silently
                # back onto the inline path — the exact freeze this
                # deletes, reappearing on one spelling only.
                from aegis.commands import resolve_deferred
                hit = resolve_deferred(payload)
                if hit is not None:
                    self._start_deferred(payload, *hit, width)
                    return
                result = await dispatch(
                    payload, CommandContext(bridge=self.app,
                                            handle=self.handle))
                delivered = self._apply_command_result(result, width)
                if delivered is None:
                    return
                text = delivered
            else:
                text = payload   # "//foo" → deliver "/foo" as a normal message
        # Every text-box message flows through the one inbox queue. When
        # idle it lands immediately (rendered by _on_core_dispatch); when
        # the agent is mid-turn it queues as a click-to-dequeue chip.
        from aegis.queue import InboxMessage, now_iso, sender_user
        msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                           body=text)
        self._record_first_user_message(text)
        self._flush_streaming()
        # Interrupt-send (alt/ctrl+enter): cut the live turn first so the
        # message lands now as the next turn instead of queuing behind it.
        # Idle → nothing to interrupt; falls through to a normal deliver.
        if event.kind == "interrupt" and self.state is AgentState.working:
            # drain=False: our deliver() below drains the buffer, so this
            # message and anything already queued go out as ONE turn.
            await self._core.interrupt(drain=False)
        receipt = await self._core.deliver(msg)
        if receipt.disposition == "queued":
            self.query_one(PendingStrip).add(msg)

    def _record_first_user_message(self, text: str) -> None:
        """Fire the first-user-message hook exactly once. Never let a
        history-write failure break a turn."""
        if (text and not self._first_msg_recorded
                and self._on_first_user_message is not None):
            self._first_msg_recorded = True
            try:
                self._on_first_user_message(text)
            except Exception:
                pass

    def _submit(self, text: str) -> None:
        """Programmatic turn (opening prompt). Direct send — bypasses the
        inbox queue; the text-box path uses deliver()."""
        self._record_first_user_message(text)
        self._flush_streaming()
        width = self._transcript().size.width or 80
        self._mount_block(
            render_user_line(text, self._palette, width), text)
        self._start_indicator()
        self.run_worker(self._core.send(text),
                        group="turn", exclusive=True)

    def on_chip_dequeued(self, event: Chip.Dequeued) -> None:
        """A queued user message was clicked: cancel it before dispatch."""
        event.stop()
        self._core.cancel_pending(event.msg)
        self.query_one(PendingStrip).remove_msg(event.msg)

    async def deliver_handoff(self, from_handle: str,
                              context: str) -> None:
        self._submit(f"[handoff from {from_handle}] {context}")

    # --- streaming aggregation -------------------------------------

    def _flush_streaming(self) -> None:
        # Whatever the repaint throttle skipped, the settled block must
        # still show. The text branch below re-renders anyway; the
        # thinking branch does not, so this is its only catch-up.
        self._catch_up_streaming_block()
        # A text stream is rendered cheaply as plain Text per delta (no
        # per-token Markdown re-parse). Finalize it to a single Markdown
        # render on flush so the settled block carries proper formatting.
        if (self._streaming_kind == "text"
                and self._streaming_block is not None
                and self._streaming_text.strip()):
            r = Markdown(self._streaming_text)
            self._streaming_block.update_content(r, self._streaming_text)
            if self._streaming_history_idx is not None:
                rec = self._history[self._streaming_history_idx]
                rec.renderable = r
                rec.payload = self._streaming_text
        self._streaming_block = None
        self._streaming_kind = None
        self._streaming_text = ""
        self._streaming_history_idx = None
        self._streaming_thinking_est = 0

    def _render_for_stream(self, kind: str,
                            text: str) -> RenderableType:
        if kind == "thinking":
            started = self._thinking_started_at or time.monotonic()
            return _thought_summary(
                time.monotonic() - started, len(text), self._palette,
                self._streaming_thinking_est)
        # Stream deltas as cheap plain Text — a fresh Markdown(full_text)
        # per token is O(n^2) parsing over the message. The block is
        # re-rendered once as Markdown in _flush_streaming when the stream
        # settles.
        return Text(text)

    def _stream_append(self, kind: str, new_text: str,
                       token_estimate: int = 0) -> None:
        if self._streaming_kind != kind:
            self._flush_streaming()
            self._streaming_kind = kind
            self._streaming_text = new_text
            self._streaming_thinking_est = token_estimate
            if kind == "thinking":
                self._thinking_started_at = time.monotonic()
            r = self._render_for_stream(kind, self._streaming_text)
            blk = self._mount_block(r, self._streaming_text)
            # _mount_block records without mounting when the tail is
            # truncated (scrolled up). Don't hold a reference to a widget
            # that isn't in the tree — the record stays authoritative and
            # jump_to_end re-links it.
            self._streaming_block = (
                blk if (self._mounted_blocks
                        and self._mounted_blocks[-1] is blk) else None)
            # Mounting is itself a reflow, so it starts the repaint window.
            self._last_repaint_at = time.monotonic()
            self._repaint_pending = False
            # The block just appended is the last entry in _history.
            self._streaming_history_idx = len(self._history) - 1
        else:
            self._streaming_text += new_text
            self._streaming_thinking_est = max(
                self._streaming_thinking_est, token_estimate)
            if self._streaming_block is not None:
                r = self._render_for_stream(
                    kind, self._streaming_text)
                # The record is the source of truth and always current; the
                # widget only has to agree with it when someone can see it,
                # and no more often than STREAM_REPAINT_S. A repaint is a
                # refresh(layout=True), i.e. a full compositor rebuild whose
                # cost is linear in mounted widgets; deltas that arrive
                # back-to-back coalesce on their own, but a real stream
                # arrives with gaps and was buying one reflow apiece. A
                # background tab skips the paint entirely — same contract,
                # on_show catches it up.
                if self.display and self._due_for_repaint():
                    self._paint_streaming(r)
                else:
                    self._repaint_pending = True
                    self._arm_repaint_flush()
                if self._streaming_history_idx is not None:
                    rec = self._history[self._streaming_history_idx]
                    rec.renderable = r
                    rec.payload = self._streaming_text

    # --- event handlers --------------------------------------------

    def _on_core_dispatch(self, _core, batch) -> None:
        """A buffered batch is leaving the queue to start a turn. User
        text-box messages render as user lines here (and shed their chip);
        agent/queue messages were already rendered on arrival by
        _on_core_inbox."""
        from textual.css.query import NoMatches
        try:
            strip = self.query_one(PendingStrip)
        except NoMatches:
            # The pane was pruned while this batch was in flight (app
            # tearing down). The observer is called off Textual's own
            # dispatch, so the failure lands as a logged ERROR rather than
            # anywhere useful — and there is nothing left to render into.
            return
        width = self._transcript().size.width or 80
        for msg in batch:
            if msg.sender == "user":
                strip.remove_msg(msg)
                self._flush_streaming()
                self._mount_block(
                    render_user_line(msg.body, self._palette, width),
                    msg.body)

    def _on_loop_change(self, _core, state, reason: str) -> None:
        """Drive the StatusBar loop segment, and toast on termination.

        A loop that ends for any reason other than a plain operator stop —
        capped, interrupted, killed by a harness error — should say so rather
        than just vanishing from the status bar.
        """
        bar = self._bar()
        if bar is not None:
            bar.set_loop(state.status() if state is not None else None)
        if state is None and reason != "stopped":
            self.app.notify(f"loop {reason}", timeout=5.0)

    def _on_core_inbox(self, _core, msg) -> None:
        """Render an incoming inbox message (handoff / queue callback) as a
        distinct block in the transcript before the agent reacts. Fires on
        every deliver(), whether the session was idle
        or buffering mid-turn. User text-box messages are owned by the
        chip/dispatch flow, so they're skipped here."""
        if msg.sender == "user":
            return
        self._flush_streaming()
        renderable = render_inbox_block(msg, self._palette)
        # Plain-text clipboard payload mirrors the substrate header
        # convention so copy-on-click gives the same shape the agent saw.
        from aegis.queue.schema import render_inbox_header
        payload = f"{render_inbox_header(msg)}\n{msg.body or ''}"
        self._mount_block(renderable, payload)

    def _on_core_event(self, _core, ev) -> None:
        # Before any routing: a subagent plan returns early below, but the
        # dock still needs to learn about it.
        if isinstance(ev, AgentPlan):
            self._refresh_plan_surfaces()
        parent = getattr(ev, "parent_tool_use_id", None)
        if parent and parent in self._subagent_boxes:
            self._route_into_box(parent, ev)     # subagent child → its box
            self.refresh_metrics()
            return
        if isinstance(ev, ToolResult) and ev.tool_call_id in self._subagent_boxes:
            self._close_box(ev.tool_call_id, ev)  # Task result closes its box
            self.refresh_metrics()
            return
        if (isinstance(ev, ToolUse) and ev.name in _SUBAGENT_TOOLS
                and ev.tool_call_id):
            self._open_box(ev)
            self.refresh_metrics()
            return
        if isinstance(ev, ThinkingTokens):
            # Invisible: only nudges the live thinking-token counter + the
            # "% think" status segment. No flush — it interleaves with the
            # thinking block that carries the final estimate.
            self.refresh_metrics()
            return
        if isinstance(ev, AssistantText):
            if ev.text:
                self._stream_append("text", ev.text)
        elif isinstance(ev, AssistantThinking):
            self._stream_append("thinking", ev.text or "", ev.token_estimate)
        elif isinstance(ev, ToolResult) and self._fold_tool_result(ev):
            pass  # folded into its ToolUse block
        elif isinstance(ev, ToolUse) and ev.tool_call_id:
            self._flush_streaming()
            # Open a live track: render the line with a running spinner+timer
            # and make the block click-to-expand its args.
            track = _ToolTrack(ev=ev, idx=len(self._history),
                               start=time.monotonic())
            renderable = render_tool_use(ev, self._palette, elapsed=0.0,
                                         running=True, frame=self._spin_frame)
            self._mount_block(renderable, _payload_for_event(ev),
                              tool_call_id=ev.tool_call_id,
                              file_target=file_target(
                                  ev.name, ev.raw_input, ev.locations,
                                  host=self._host),
                              remote_path=self._remote_path_for(ev))
            # Remember this call's block so its (possibly out-of-order,
            # parallel) ToolResult folds in below instead of appending.
            self._tool_use_idx[ev.tool_call_id] = track.idx
            self._tools[ev.tool_call_id] = track
            self._ensure_tool_timer()
        elif isinstance(ev, UserMessage):
            # Already on screen: the pane mounts the user's line at send
            # time. This is claude's --replay-user-messages echo, which the
            # log keeps so replay can rebuild the dialogue — but rendering
            # it here would print the message a second time.
            pass
        elif isinstance(ev, AgentPlan) and self._replace_plan_block(ev):
            pass  # the plan mutated in place; no new block
        else:
            self._flush_streaming()
            renderable = render_event(ev, self._palette)
            if renderable is not None:
                block = self._mount_block(renderable, _payload_for_event(ev))
                if isinstance(ev, Result):
                    self._adopt_result_block(block, ev)
                if isinstance(ev, AgentPlan):
                    self._plan_blocks[self._plan_key(ev)] = (
                        block, len(self._history) - 1)
        self.refresh_metrics()

    def _adopt_result_block(self, block, ev) -> None:
        """Make this the terminator that carries a live age, and strip the
        age off the one it replaces."""
        prev = self._last_result
        if prev is not None:
            self._paint_result(prev, age_s=None)
        self._last_result = _ResultBlock(
            block=block, ev=ev, idx=len(self._history) - 1,
            ended_at=time.time())
        self.refresh_result_age()

    def _paint_result(self, r: _ResultBlock, *, age_s: float | None) -> None:
        renderable = render_event(r.ev, self._palette, age_s=age_s)
        if renderable is None:
            return
        payload = _payload_for_event(r.ev)
        if 0 <= r.idx < len(self._history):
            self._history[r.idx].renderable = renderable
        with contextlib.suppress(Exception):
            r.block.update_content(renderable, payload)

    def refresh_result_age(self) -> None:
        """Re-stamp the newest terminator with how long ago the turn ended.

        Driven by the app's one-second tick for the pane you're looking at,
        and on show — coming back to a tab is exactly when the answer
        matters. Repaints only when the rendered string actually changes.
        """
        r = self._last_result
        if r is None:
            return
        age = time.time() - r.ended_at
        shown = format_age(age)
        if shown == r.shown:
            return
        r.shown = shown
        self._paint_result(r, age_s=age)

    def _fold_tool_result(self, ev: ToolResult) -> bool:
        """Render a ToolResult *inside* its matching ToolUse block. Returns
        False (→ caller appends it as a standalone block) when there's no
        known matching call — e.g. the use scrolled out of the window."""
        tid = ev.tool_call_id or ""
        track = self._tools.get(tid)
        if track is None:
            return False
        self._flush_streaming()
        result_r = render_event(ev, self._palette)
        if result_r is None:
            result_r = Text("")
        # Freeze the timer and attach the result to the track, then re-render.
        track.done = True
        track.elapsed = time.monotonic() - track.start
        track.result_r = result_r
        rec = self._history[track.idx]
        rec.payload = f"{rec.payload}\n{_payload_for_event(ev)}"
        self._render_tool_block(track, scroll=True)
        if not self._any_spinner_running():
            self._stop_tool_timer()
        return True

    # --- per-tool spinner + timer + expandable args ----------------

    # --- deferred commands (/btw, @peer) ---------------------------

    def _start_deferred(self, payload: str, cmd, args, width: int) -> None:
        """Mount a placeholder and run ``payload`` off the input handler.

        The placeholder is what fills the gap the await used to occupy: a
        spinner, the question echoed back, and a timer, all ticking on the
        existing per-tool 10 Hz ticker.
        """
        if self._deferred is not None and not self._deferred.done:
            # One at a time, per pane. Two spinners racing would leave ESC
            # with no defensible answer about which it cancels. Everything
            # else stays available — refusing a second deferred command is
            # a guard; refusing the rest would just be a lock replacing a
            # freeze.
            from aegis.commands import CommandResult
            from aegis.render import render_command_block
            running = self._deferred
            self._flush_streaming()
            self._mount_block(
                render_command_block(
                    CommandResult(
                        False, f"{running.label} is already running",
                        "ESC to cancel it"),
                    self._palette, width),
                f"{running.label} is already running")
            return
        label = "btw" if cmd.name == "btw" else f"/{cmd.name}"
        subject = " ".join(str(v) for v in args.positional.values() if v)
        self._flush_streaming()
        self._mount_block(Text(""), "")
        track = _DeferredTrack(
            idx=len(self._history) - 1, start=time.monotonic(),
            label=label, subject=subject,
            cancel_note=cmd.resolved_cancel_note(args))
        self._deferred = track
        self._render_deferred_block(track)
        self._ensure_tool_timer()
        track.worker = self.run_worker(
            self._run_deferred(payload, track, width), exclusive=False)

    async def _run_deferred(self, payload: str, track: "_DeferredTrack",
                            width: int) -> None:
        """Dispatch on a worker, then rewrite the placeholder in place."""
        from aegis.commands import CommandContext, dispatch
        result = await dispatch(
            payload, CommandContext(bridge=self.app, handle=self.handle))
        if track.done or self._deferred is not track:
            # Cancelled while in flight — the tombstone already owns this
            # block, and a late answer must not overwrite it. A side
            # question must never disturb the conversation it sits beside,
            # and that includes on the way out.
            return
        track.done = True
        track.elapsed = time.monotonic() - track.start
        self._deferred = None
        # at_idx: the answer lands in the block the command mounted, not
        # at the tail — so a note stays where you asked it while the
        # agent's output streams past underneath.
        self._apply_command_result(result, width, at_idx=track.idx)
        if not self._any_spinner_running():
            self._stop_tool_timer()

    def _render_deferred_block(self, track: "_DeferredTrack", *,
                               cancelled: bool = False) -> None:
        from aegis.render import render_deferred
        elapsed = (track.elapsed if track.elapsed is not None
                   else time.monotonic() - track.start)
        self._put_block(
            render_deferred(track.label, track.subject, elapsed,
                            self._palette, frame=self._spin_frame,
                            cancelled=cancelled,
                            cancel_note=track.cancel_note),
            (f"{track.label} · {track.cancel_note}" if cancelled
             else f"{track.label} · {track.subject}"),
            at_idx=track.idx)

    def _any_spinner_running(self) -> bool:
        """Whether the 10 Hz ticker still has anything to animate."""
        return self._any_tool_running() or (
            self._deferred is not None and not self._deferred.done)

    def _any_tool_running(self) -> bool:
        return any(not t.done for t in self._tools.values())

    def _ensure_tool_timer(self) -> None:
        # Background panes don't animate spinners — on_show restarts the
        # timer when the tab is brought forward.
        if not self.display:
            return
        if self._tool_timer is None:
            # 0.1s cadence + tenths in _fmt_dur → the timer visibly ticks
            # sub-second, like the WorkingIndicator.
            self._tool_timer = self.set_interval(0.1, self._tick_tools)

    def _stop_tool_timer(self) -> None:
        if self._tool_timer is not None:
            with contextlib.suppress(Exception):
                self._tool_timer.stop()
            self._tool_timer = None

    def _tick_tools(self) -> None:
        if not self._any_spinner_running():
            self._stop_tool_timer()
            return
        self._spin_frame += 1
        for track in self._tools.values():
            if not track.done:
                self._render_tool_block(track, layout=False)
        if self._deferred is not None and not self._deferred.done:
            self._render_deferred_block(self._deferred)

    def _render_tool_block(self, track: "_ToolTrack",
                           *, scroll: bool = False,
                           layout: bool = True) -> None:
        """(Re)render a tool-call block from its track — running spinner+timer,
        frozen duration, folded result, and expanded args as applicable.

        ``layout=False`` on the 10 Hz tick: only the elapsed digits change,
        on a line the block already occupies. Attaching a result or
        expanding args genuinely grows the block and keeps the default."""
        running = not track.done
        elapsed = (time.monotonic() - track.start) if running else track.elapsed
        line = render_tool_use(track.ev, self._palette, elapsed=elapsed,
                               running=running, frame=self._spin_frame,
                               expanded=track.expanded)
        rend = Group(line, track.result_r) if track.result_r is not None \
            else line
        rec = self._history[track.idx]
        rec.renderable = rend
        pos = track.idx - self._window_start
        if 0 <= pos < len(self._mounted_blocks):
            self._mounted_blocks[pos].update_content(
                rend, rec.payload, layout=layout)
            if scroll and self._stick_to_bottom:
                self._transcript().scroll_end(animate=False)

    def _freeze_all_tools(self) -> None:
        """Turn ended (or was interrupted): stop every running tool timer,
        freeze its elapsed, and stop the ticker."""
        for track in self._tools.values():
            if not track.done:
                track.done = True
                if track.elapsed is None:
                    track.elapsed = time.monotonic() - track.start
                # Spinner glyph -> frozen duration, same single line.
                self._render_tool_block(track, layout=False)
        # A running side note is NOT frozen by the turn ending. /btw reads
        # the log rather than the session — that independence is the whole
        # reason it is legal mid-turn — so a turn finishing underneath one
        # must leave its spinner ticking.
        if not self._any_spinner_running():
            self._stop_tool_timer()

    def on_copyable_block_tool_expand_toggle(
            self, event: "CopyableBlock.ToolExpandToggle") -> None:
        event.stop()
        track = self._tools.get(event.tool_call_id)
        if track is None:
            return
        track.expanded = not track.expanded
        self._render_tool_block(track, scroll=True)

    # --- subagent (Task) grouping ----------------------------------

    def _open_box(self, ev: ToolUse) -> None:
        """A Task dispatch opens a SubagentBox, mounted as ONE transcript
        block. Its child events (parent_tool_use_id == this id) route inside."""
        self._flush_streaming()
        summary = ev.summary or ev.name
        self._subagent_summary[ev.tool_call_id] = summary
        self._subagent_counts[ev.tool_call_id] = 0
        header = self._box_header(summary, running=True, count=0)
        payload = _payload_for_event(ev)
        box = SubagentBox(header, payload, self._palette)
        self._history.append(BlockRecord(header, payload, False))
        t = self._transcript()
        ind = self._working_indicator()
        if ind is not None and ind.parent is t:
            t.mount(box, before=ind)
        else:
            t.mount(box)
        self._mounted_blocks.append(box)
        self._subagent_boxes[ev.tool_call_id] = box
        if self._stick_to_bottom:
            t.scroll_end(animate=False)

    def _route_into_box(self, tid: str, ev) -> None:
        box = self._subagent_boxes[tid]
        result_r = (render_event(ev, self._palette)
                    if isinstance(ev, ToolResult) else None)
        if result_r is not None and box.fold_child_result(
                result_r, _payload_for_event(ev)):
            pass  # folded into the box's last child (in-box tool pairing)
        else:
            r = render_event(ev, self._palette)
            if r is not None:
                box.add_child(r, _payload_for_event(ev),
                              tight=isinstance(ev, ToolUse))
        self._subagent_counts[tid] += 1
        box.set_header(
            self._box_header(self._subagent_summary[tid], running=True,
                             count=self._subagent_counts[tid]),
            box._header_payload)
        if self._stick_to_bottom:
            self._transcript().scroll_end(animate=False)

    def _close_box(self, tid: str, ev: ToolResult) -> None:
        box = self._subagent_boxes[tid]
        icon = "✗" if ev.is_error else "✓"
        box.set_header(
            self._box_header(self._subagent_summary[tid], running=False,
                             count=self._subagent_counts[tid], icon=icon),
            box._header_payload)
        result_r = render_event(ev, self._palette)
        if result_r is not None:
            box.close(result_r, _payload_for_event(ev))

    def _box_header(self, summary: str, *, running: bool, count: int,
                    icon: str = "✓") -> Text:
        status = "⏳" if running else icon
        return Text.assemble(("🤖 ", self._palette.accent),
                             f"{summary} · {status} {count} events")

    def _refresh_plan_surfaces(self) -> None:
        """Push the session's plan into the strip (and, once open, the
        dock). Tolerant of a pane whose widgets are not mounted yet or are
        already torn down — this fires from observer callbacks."""
        core = self._core
        if core is None or not hasattr(core, "plan_state"):
            return
        try:
            strip = self.query_one("#plan-strip", PlanStrip)
        except Exception:
            return
        strip.refresh_plan(core.plan_state(), core.plan.working)
        try:
            dock = self.query_one("#plan-dock", PlanDock)
        except Exception:
            return
        dock.refresh_plan(core.plan_state(), core.subplan_states(),
                          core.plan.working)

    def _on_core_state(self, _core, state: AgentState,
                       finished: bool) -> None:
        bar = self._bar()
        if bar is not None:
            bar.set_state(state)
        # Input outline echoes the state dot: vivid when idle (a live agent
        # that acts on your message now) vs subdued while working (the message
        # queues behind the turn). See the `.working` CSS rule.
        self.set_class(state is AgentState.working, "working")
        # The plan spinner turns iff working time is accruing, so it has to
        # follow turn state, not just plan events.
        self._refresh_plan_surfaces()
        # Reconcile the working indicator to the live state: visible iff the
        # agent is working. Keying off `finished` alone orphaned the spinner
        # on interrupt (which emits `ready, finished=False`) — so it "a veces
        # se queda". Keying off the state also self-heals a stale/frozen
        # indicator on a self-woken or chained turn.
        if state is AgentState.working:
            self._start_indicator()
        else:
            self._stop_indicator()
        if finished and state is AgentState.error \
                and not self._transcript_has("⚠ harness"):
            self._flush_streaming()
            err = getattr(self._core, "last_error", None)
            label = (f"⚠ harness error: {type(err).__name__}: {err}"
                     if err is not None else "⚠ harness error")
            self._mount_block(
                Text(label, style=self._palette.err), label)
        self.post_message(PaneStateChanged(self, finished))
        if finished:
            self._freeze_all_tools()
            inp = self.query_one(GrowingInput)
            inp.disabled = False
            # Only re-focus the input if this pane is the visible one.
            # A background pane finishing its turn must not steal focus
            # from whatever the user is typing into the active tab.
            if self.display:
                inp.focus()
        self.refresh_metrics()

    def interrupt(self, *, drain: bool = True):
        """Cut the live turn. Returns the Textual worker doing it (None when
        there was nothing to interrupt) so callers that need the interrupt to
        have actually landed — e.g. AegisApp.interrupt, whose AppBridge
        contract is that a following deliver() sees a settled session — can
        await it."""
        if self.state is not AgentState.working:
            return None

        async def _do() -> None:
            await self._core.interrupt(drain=drain)
            self._flush_streaming()
            self._mount_block(
                Text("^C — interrupted", style=self._palette.muted),
                "^C — interrupted")
            self.refresh_metrics()
            inp = self.query_one(GrowingInput)
            inp.disabled = False
            inp.focus()

        return self.run_worker(_do(), group="turn", exclusive=True)

    def show_resume_banner(self, text: str) -> None:
        """Mount a single banner line at the top of this pane's transcript."""
        from textual.widgets import Static
        banner = Static(text, classes="resume-banner")
        self._transcript().mount(banner, before=self._transcript().children[0]
                                 if self._transcript().children else None)

    def show_resume_failure(self, reason: str) -> None:
        """Mount a styled failure banner at the top of this pane's transcript.

        Used when driver.resume(...) raised for this tab. The pane stays open
        so Alex can inspect the reason and close it manually.
        """
        from textual.widgets import Static
        text = Text(f"⚠ resume failed: {reason}", style="bold red")
        banner = Static(text, classes="resume-failure")
        self._transcript().mount(banner, before=self._transcript().children[0]
                                 if self._transcript().children else None)

    def clear_transcript(self) -> None:
        """Clear _history and remove all mounted transcript blocks.

        Called on ``window_reset`` stream events so stale content is wiped
        before the server replays fresh events for this session.
        """
        import contextlib
        self._history.clear()
        self._window_start = 0
        self._window_end = 0
        if hasattr(self, "_streaming_block"):
            self._flush_streaming()
        for b in list(getattr(self, "_mounted_blocks", [])):
            with contextlib.suppress(Exception):
                b.remove()
        if hasattr(self, "_mounted_blocks"):
            self._mounted_blocks.clear()
        if hasattr(self, "_tool_use_idx"):
            self._tool_use_idx.clear()
        if hasattr(self, "_tools"):
            self._tools.clear()
        if hasattr(self, "_subagent_boxes"):
            self._subagent_boxes.clear()
        if hasattr(self, "_subagent_counts"):
            self._subagent_counts.clear()
        if hasattr(self, "_subagent_summary"):
            self._subagent_summary.clear()

    async def close(self) -> None:
        await self._core.close()
