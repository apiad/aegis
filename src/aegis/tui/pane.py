from __future__ import annotations

import contextlib
import re
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.drivers.base import HarnessSession
from aegis.events import AssistantText, AssistantThinking, ToolResult, ToolUse
from aegis.render import (
    coalesce_chunks, render_event, render_inbox_block, render_tool_use,
    render_user_line,
)
from aegis.state.session_log import EventReplay, make_session_log_observer
from aegis.tui.state import AgentState
from aegis.tui.pending import Chip, PendingStrip
from aegis.tui.strip import QueueStrip
from aegis.tui.widgets import GrowingInput, StatusBar
from aegis.transcript_constants import (  # noqa: F401  (re-exported)
    N_MAX, REPLAY_TAIL, EVICT_BATCH, LOAD_BATCH, STICKY_EPS, LOAD_MORE_EPS,
    DEBOUNCE_S,
)


# Names of the tool that dispatches a subagent. Claude Code has used both
# across versions ("Task" historically, "Agent" as of 2.1.x); match both so
# subagent events group into a box regardless of the running CLI's naming.
_SUBAGENT_TOOLS = frozenset({"Task", "Agent"})


@dataclass(slots=True)
class BlockRecord:
    """One transcript entry. Mutable so streaming aggregation can update
    in place. Mirrors the arguments passed to CopyableBlock so older
    blocks can be reconstructed on scroll-up."""
    renderable: RenderableType
    payload: str
    tight: bool
    tool_call_id: str | None = None


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


def replay_blocks(replay: EventReplay, colors=None) -> list[RenderableType]:
    """Render replay events as a list of Rich renderables, in order,
    using the live render path. Appends a ⚠ interrupted marker if
    replay.interrupted. Returns an empty list for an empty replay.
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

    def start(self) -> None:
        self.add_class("-active")
        self._started_at = time.monotonic()
        self._frame = 0
        self._verb_idx = random.randrange(len(_VERBS))
        self._refresh()
        # Spinner + timer redraw at 100ms; verb rotates every 5s.
        self._tick_timer = self.set_interval(0.1, self._tick)
        self._verb_timer = self.set_interval(5.0, self._rotate_verb)

    def stop(self) -> None:
        self.remove_class("-active")
        self._started_at = None
        for t in (self._tick_timer, self._verb_timer):
            if t is not None:
                with contextlib.suppress(Exception):
                    t.stop()
        self._tick_timer = None
        self._verb_timer = None
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
        self.update(Text(
            f"{spinner}  {verb}…  {elapsed}",
            style=f"italic {self._palette.muted}",
        ))


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


class CopyableBlock(Widget):
    """One transcript cell — hover tints, click copies its text payload.

    The visible content can be updated in place via ``update_content``
    so that streaming text events (token-by-token AssistantText /
    AssistantThinking) accumulate into a single block rather than
    fragmenting into many short ones.
    """

    DEFAULT_CSS = """
    CopyableBlock { height: auto; padding: 0 1; margin-bottom: 1;
                    background: $background; }
    /* Tight blocks have no margin below — used to glue a tool call
       (⏺) to its result (└ ok) so they read as one paired unit. */
    CopyableBlock.-tight { margin-bottom: 0; }
    CopyableBlock:hover { background: $surface; }
    CopyableBlock > .content { background: transparent; height: auto; }
    """

    class ToolExpandToggle(Message):
        """A tool-call block was clicked — toggle its collapsed args."""
        def __init__(self, tool_call_id: str) -> None:
            super().__init__()
            self.tool_call_id = tool_call_id

    def __init__(self, renderable: RenderableType,
                 text_payload: str, *, tight: bool = False,
                 tool_call_id: str | None = None) -> None:
        super().__init__(classes="-tight" if tight else None)
        self._renderable = renderable
        self._text_payload = text_payload
        self._tool_call_id = tool_call_id
        self._backtick_tokens: list[str] = _extract_backtick_tokens(
            text_payload)
        # Textual tooltip floats above the widget on hover — no
        # layout shift, no extra row inside the block.
        if tool_call_id is not None:
            self.tooltip = "click to expand args"
        else:
            self.tooltip = (
                "click to copy | ctrl+click to open file"
                if self._backtick_tokens else "click to copy")

    def compose(self) -> ComposeResult:
        yield Static(self._renderable, classes="content")

    def update_content(self, renderable: RenderableType,
                       text_payload: str) -> None:
        self._renderable = renderable
        self._text_payload = text_payload
        self._backtick_tokens = _extract_backtick_tokens(text_payload)
        self.tooltip = (
            "click to copy | ctrl+click to open file" if self._backtick_tokens else "click to copy"
        )
        with contextlib.suppress(Exception):
            self.query_one(".content", Static).update(renderable)

    def text_payload(self) -> str:
        return self._text_payload

    def on_click(self, event: Click) -> None:
        # Tool-call blocks toggle their collapsed args instead of copying.
        if self._tool_call_id is not None:
            self.post_message(self.ToolExpandToggle(self._tool_call_id))
            return
        if event.ctrl and self._backtick_tokens:
            self._open_file_from_tokens()
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
    async def _open_file_from_tokens(self) -> None:
        from aegis.tui.picker import (
            FilePickerModal, _TokenChooser, filter_path_tokens,
            resolve_unique_match)

        cwd = Path.cwd()
        indexer = getattr(self.app, "_file_indexer", None)
        paths = (indexer.paths
                 if (indexer is not None and indexer.ready) else [])
        tokens = filter_path_tokens(self._backtick_tokens, cwd, paths)
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
    DEFAULT_CSS = """
    ConversationPane { layout: vertical; height: 1fr;
                       background: $background; }
    ConversationPane #transcript { height: 1fr; background: $background;
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

    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_slug: str, handle: str, palette,
                 *, digest=None, state_dir_path: Path | None = None,
                 replay: EventReplay | None = None,
                 core=None) -> None:
        super().__init__(id=f"pane-{handle}")
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self._digest = digest
        self._created_at: str = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.unseen = False
        # ``core`` allows remote mode to inject a RemotePaneCore directly,
        # bypassing the AgentSession wrapping that requires a real HarnessSession.
        if core is not None:
            self._core = core
        else:
            self._core = AgentSession(session, agent, agent_slug, handle)
        self._core.add_event_observer(self._on_core_event)
        self._core.add_state_observer(self._on_core_state)
        self._core.add_inbox_observer(self._on_core_inbox)
        self._core.add_dispatch_observer(self._on_core_dispatch)
        if state_dir_path is not None:
            self._core.add_event_observer(
                make_session_log_observer(state_dir_path, handle))
        self._replay = replay
        # Streaming aggregation state: while inside a run of
        # AssistantText (or AssistantThinking) events we accumulate
        # into one CopyableBlock and update it in place.
        self._streaming_block: CopyableBlock | None = None
        self._streaming_kind: str | None = None     # "text" | "thinking"
        self._streaming_text: str = ""
        # Windowing: every rendered block lives here; only
        # _history[_window_start:] is mounted. _streaming_history_idx
        # points at the record currently being mutated by streaming
        # aggregation (None when no stream is in flight).
        self._history: list[BlockRecord] = []
        self._window_start: int = 0
        self._streaming_history_idx: int | None = None
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
        for w in self.query(PendingStrip):
            w.set_palette(palette)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield VerticalScroll(id="transcript")
            if self._digest is not None:
                yield QueueStrip(self._digest, self._palette)
            # In remote mode, agent may be None; fall back to empty strings.
            _model = getattr(self._agent, "model", "") if self._agent else ""
            _perm = (getattr(self._agent.permission, "value", "")
                     if self._agent else "")
            yield StatusBar(self.handle, self.agent_slug,
                            _model, _perm, self._palette)
            yield PendingStrip(self._palette)
            yield GrowingInput(placeholder="type a message…")

    async def on_mount(self) -> None:
        self.query_one(StatusBar).set_state(AgentState.ready)
        self._mount_replay()
        self.refresh_metrics()
        t = self._transcript()
        self.watch(t, "scroll_y", self._on_scroll_y)

    def _mount_replay(self) -> None:
        """Paint prior events onto the transcript on resume.

        Builds the full ``_history`` cheaply — plain dataclass records off
        the coalesced event stream, no widgets — then mounts only the last
        ``REPLAY_TAIL`` blocks. A long resumed session paints instantly
        instead of mounting (and immediately evicting) hundreds of widgets.
        Older blocks are reconstructed on demand by ``_load_older`` when
        Alex scrolls up."""
        if self._replay is None:
            return
        records: list[BlockRecord] = []
        use_idx: dict[str, int] = {}   # tool_call_id → record index
        box_idx: dict[str, int] = {}   # Task tool_call_id → box record index
        open_box: dict[str, int] = {}  # still-open Task tool_call_id → index

        def _fold_into(idx: int, ev) -> None:
            r = render_event(ev, self._palette)
            if r is None:
                return
            rec = records[idx]
            rec.renderable = Group(rec.renderable, r)
            rec.payload = f"{rec.payload}\n{_payload_for_event(ev)}"

        for ev in coalesce_chunks(self._replay.events):
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
            r = render_event(ev, self._palette)
            if r is None:
                continue
            records.append(BlockRecord(r, _payload_for_event(ev), False))
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
        t = self._transcript()
        for rec in records[self._window_start:]:
            block = CopyableBlock(rec.renderable, rec.payload, tight=rec.tight)
            t.mount(block)
            self._mounted_blocks.append(block)
        t.scroll_end(animate=False)

    def refresh_metrics(self) -> None:
        self.query_one(StatusBar).set_metrics(
            self._core.metrics.render(time.monotonic()))

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _on_scroll_y(self, _value: float) -> None:
        t = self._transcript()
        self._stick_to_bottom = (
            (t.max_scroll_y - t.scroll_y) <= STICKY_EPS)
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
            new_blocks: list[CopyableBlock] = []
            for rec in self._history[new_start : self._window_start]:
                block = CopyableBlock(
                    rec.renderable, rec.payload, tight=rec.tight,
                    tool_call_id=rec.tool_call_id)
                if anchor is not None:
                    t.mount(block, before=anchor)
                else:
                    t.mount(block)
                new_blocks.append(block)
            # Prepend new blocks to the explicit mounted list (DOM order).
            self._mounted_blocks[:0] = new_blocks
            self._window_start = new_start

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
        matches = self.query(WorkingIndicator)
        return matches.first() if len(matches) else None

    def _apply_command_effect(self, effect: dict) -> None:
        """Apply a slash-command frontend effect (theme switch, transcript
        clear). Unknown kinds are ignored (forward-compatible)."""
        kind = effect.get("kind")
        if kind == "theme":
            self.app.theme = effect["name"]
        elif kind == "clear":
            from rich.text import Text

            from aegis.tui.metrics import _fmt_tokens
            for b in self._mounted_blocks:
                with contextlib.suppress(Exception):
                    b.remove()
            self._mounted_blocks.clear()
            self._history.clear()
            self._window_start = 0
            ctx_tokens = self._core.metrics.last_true_input
            marker = (f"──── transcript cleared · {_fmt_tokens(ctx_tokens)} "
                      f"context tokens still in play ────")
            self._mount_block(
                Text(marker, style=self._palette.muted, justify="center"),
                marker)

    def _mount_block(self, renderable: RenderableType,
                     text_payload: str,
                     *, tight: bool = False,
                     tool_call_id: str | None = None) -> CopyableBlock:
        self._history.append(
            BlockRecord(renderable, text_payload, tight, tool_call_id))
        block = CopyableBlock(renderable, text_payload, tight=tight,
                              tool_call_id=tool_call_id)
        t = self._transcript()
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
        if self._stick_to_bottom:
            t.scroll_end(animate=False)
            if len(self._history) - self._window_start > N_MAX:
                self._evict_top(EVICT_BATCH)
        return block

    def _evict_top(self, n: int) -> None:
        """Unmount the first n mounted CopyableBlocks and advance
        _window_start. Safe to call only while _stick_to_bottom is True:
        the user is at the tail, so removing widgets above the viewport
        doesn't disturb them."""
        for b in self._mounted_blocks[:n]:
            with contextlib.suppress(Exception):
                b.remove()
        del self._mounted_blocks[:n]
        self._window_start += n

    def _start_indicator(self) -> None:
        """Create + mount a WorkingIndicator at the bottom of the
        transcript, then start its animation. No-op if one is already
        mounted."""
        if self._working_indicator() is not None:
            return
        ind = WorkingIndicator(self._palette)
        self._transcript().mount(ind)
        ind.start()
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

    def _transcript_blocks(self) -> list[CopyableBlock]:
        return list(self.query(CopyableBlock))

    def _transcript_has(self, needle: str) -> bool:
        return any(needle in b.text_payload()
                   for b in self._transcript_blocks())

    def focus_input(self) -> None:
        self.query_one(GrowingInput).focus()

    def input_widget(self) -> "GrowingInput":
        return self.query_one(GrowingInput)

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
        self.set_class(value.startswith("/"), "slash-command")

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
        elif text.startswith("/"):
            # Slash family: `/cmd` is a command aegis executes directly and
            # renders in the transcript (never delivered to the agent); `//x`
            # is an escape that delivers a literal `/x` message.
            from aegis.commands import (
                CommandContext, classify_input, dispatch)
            from aegis.render import render_command_block
            kind, payload = classify_input(text)
            if kind == "command":
                width = self._transcript().size.width or 80
                result = await dispatch(
                    payload, CommandContext(bridge=self.app,
                                            handle=self.handle))
                self._flush_streaming()
                self._mount_block(
                    render_command_block(result, self._palette, width),
                    f"{result.title}\n{result.body}".strip())
                if result.effect:
                    self._apply_command_effect(result.effect)
                return
            text = payload   # "//foo" → deliver "/foo" as a normal message
        # Every text-box message flows through the one inbox queue. When
        # idle it lands immediately (rendered by _on_core_dispatch); when
        # the agent is mid-turn it queues as a click-to-dequeue chip.
        from aegis.queue import InboxMessage, now_iso, sender_user
        msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                           body=text)
        self._flush_streaming()
        # Interrupt-send (alt/ctrl+enter): cut the live turn first so the
        # message lands now as the next turn instead of queuing behind it.
        # Idle → nothing to interrupt; falls through to a normal deliver.
        if event.kind == "interrupt" and self.state is AgentState.working:
            await self._core.interrupt()
        receipt = await self._core.deliver(msg)
        if receipt.disposition == "queued":
            self.query_one(PendingStrip).add(msg)

    def _submit(self, text: str) -> None:
        """Programmatic turn (opening prompt). Direct send — bypasses the
        inbox queue; the text-box path uses deliver()."""
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
        self._streaming_block = None
        self._streaming_kind = None
        self._streaming_text = ""
        self._streaming_history_idx = None

    def _render_for_stream(self, kind: str,
                            text: str) -> RenderableType:
        if kind == "thinking":
            body = text.strip()
            if not body:
                return Text("✻ Thinking…", style=self._palette.muted)
            return Text(f"✻ {body}",
                        style=f"italic {self._palette.muted}")
        return Markdown(text) if text.strip() else Text("")

    def _stream_append(self, kind: str, new_text: str) -> None:
        if self._streaming_kind != kind:
            self._flush_streaming()
            self._streaming_kind = kind
            self._streaming_text = new_text
            r = self._render_for_stream(kind, self._streaming_text)
            self._streaming_block = self._mount_block(
                r, self._streaming_text)
            # The block just appended is the last entry in _history.
            self._streaming_history_idx = len(self._history) - 1
        else:
            self._streaming_text += new_text
            if self._streaming_block is not None:
                r = self._render_for_stream(
                    kind, self._streaming_text)
                self._streaming_block.update_content(
                    r, self._streaming_text)
                if self._streaming_history_idx is not None:
                    rec = self._history[self._streaming_history_idx]
                    rec.renderable = r
                    rec.payload = self._streaming_text

    # --- event handlers --------------------------------------------

    def _on_core_dispatch(self, _core, batch) -> None:
        """A buffered batch is leaving the queue to start a turn. User
        text-box messages render as user lines here (and shed their chip);
        agent/queue/telegram messages were already rendered on arrival by
        _on_core_inbox."""
        strip = self.query_one(PendingStrip)
        width = self._transcript().size.width or 80
        for msg in batch:
            if msg.sender == "user":
                strip.remove_msg(msg)
                self._flush_streaming()
                self._mount_block(
                    render_user_line(msg.body, self._palette, width),
                    msg.body)

    def _on_core_inbox(self, _core, msg) -> None:
        """Render an incoming inbox message (handoff / queue callback /
        telegram) as a distinct block in the transcript before the agent
        reacts. Fires on every deliver(), whether the session was idle
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
        if isinstance(ev, AssistantText):
            if ev.text:
                self._stream_append("text", ev.text)
        elif isinstance(ev, AssistantThinking):
            self._stream_append("thinking", ev.text or "")
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
                              tool_call_id=ev.tool_call_id)
            # Remember this call's block so its (possibly out-of-order,
            # parallel) ToolResult folds in below instead of appending.
            self._tool_use_idx[ev.tool_call_id] = track.idx
            self._tools[ev.tool_call_id] = track
            self._ensure_tool_timer()
        else:
            self._flush_streaming()
            renderable = render_event(ev, self._palette)
            if renderable is not None:
                self._mount_block(renderable, _payload_for_event(ev))
        self.refresh_metrics()

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
        if not self._any_tool_running():
            self._stop_tool_timer()
        return True

    # --- per-tool spinner + timer + expandable args ----------------

    def _any_tool_running(self) -> bool:
        return any(not t.done for t in self._tools.values())

    def _ensure_tool_timer(self) -> None:
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
        if not self._any_tool_running():
            self._stop_tool_timer()
            return
        self._spin_frame += 1
        for track in self._tools.values():
            if not track.done:
                self._render_tool_block(track)

    def _render_tool_block(self, track: "_ToolTrack",
                           *, scroll: bool = False) -> None:
        """(Re)render a tool-call block from its track — running spinner+timer,
        frozen duration, folded result, and expanded args as applicable."""
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
            self._mounted_blocks[pos].update_content(rend, rec.payload)
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
                self._render_tool_block(track)
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

    def _on_core_state(self, _core, state: AgentState,
                       finished: bool) -> None:
        self.query_one(StatusBar).set_state(state)
        # Input outline echoes the state dot: vivid when idle (a live agent
        # that acts on your message now) vs subdued while working (the message
        # queues behind the turn). See the `.working` CSS rule.
        self.set_class(state is AgentState.working, "working")
        # A turn is starting (landed input, chained inbox batch, or
        # programmatic send) — keep the working indicator pinned. No-op if
        # one is already mounted.
        if not finished and state is AgentState.working:
            self._start_indicator()
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
            self._stop_indicator()
            inp = self.query_one(GrowingInput)
            inp.disabled = False
            # Only re-focus the input if this pane is the visible one.
            # A background pane finishing its turn must not steal focus
            # from whatever the user is typing into the active tab.
            if self.display:
                inp.focus()
        self.refresh_metrics()

    def interrupt(self) -> None:
        if self.state is not AgentState.working:
            return

        async def _do() -> None:
            await self._core.interrupt()
            self._flush_streaming()
            self._mount_block(
                Text("^C — interrupted", style=self._palette.muted),
                "^C — interrupted")
            self.refresh_metrics()
            inp = self.query_one(GrowingInput)
            inp.disabled = False
            inp.focus()

        self.run_worker(_do(), group="turn", exclusive=True)

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
