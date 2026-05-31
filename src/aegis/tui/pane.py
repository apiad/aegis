from __future__ import annotations

import contextlib
import re
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.drivers.base import HarnessSession
from aegis.events import AssistantText, AssistantThinking, ToolUse
from aegis.render import (
    coalesce_chunks, render_event, render_inbox_block, render_user_line,
)
from aegis.state.session_log import EventReplay
from aegis.tui.state import AgentState
from aegis.tui.strip import QueueStrip
from aegis.tui.widgets import GrowingInput, StatusBar


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


def make_session_log_observer(state_dir_path: Path, handle: str):
    """Returns an EventCb that appends every event to the per-tab JSONL."""
    from aegis.state.session_log import append_event

    def _obs(_sess, ev):
        try:
            append_event(state_dir_path, handle, ev)
        except Exception:
            # Persistence must never break the live render.
            pass

    return _obs


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

    def __init__(self, renderable: RenderableType,
                 text_payload: str, *, tight: bool = False) -> None:
        super().__init__(classes="-tight" if tight else None)
        self._renderable = renderable
        self._text_payload = text_payload
        self._backtick_tokens: list[str] = _extract_backtick_tokens(
            text_payload)
        # Textual tooltip floats above the widget on hover — no
        # layout shift, no extra row inside the block.
        self.tooltip = (
            "click to copy | ctrl+click to open file" if self._backtick_tokens else "click to copy"
        )

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
        tokens = self._backtick_tokens
        if len(tokens) == 1:
            token = tokens[0]
        else:
            from aegis.tui.picker import _TokenChooser
            token = await self.app.push_screen_wait(_TokenChooser(tokens))
            if token is None:
                return

        from aegis.tui.picker import FilePickerModal, resolve_unique_match
        opener = getattr(self.app, "_open_file_tab", None)

        # Bypass the picker when the token resolves unambiguously.
        indexer = getattr(self.app, "_file_indexer", None)
        paths = (indexer.paths
                 if (indexer is not None and indexer.ready) else [])
        match = resolve_unique_match(token, paths)
        if match is not None:
            candidate = Path.cwd() / match
            if candidate.is_file() and opener is not None:
                await opener(candidate)
                return

        path = await self.app.push_screen_wait(FilePickerModal(prefill=token))
        if path is not None and opener is not None:
            await opener(path)


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
                             border-top: solid $foreground 20%;
                             border-bottom: solid $foreground 20%;
                             margin-top: 1;
                             scrollbar-size: 0 0; }
    ConversationPane GrowingInput:focus { border: none;
                             border-top: solid $foreground 20%;
                             border-bottom: solid $foreground 20%; }
    """

    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_slug: str, handle: str, palette,
                 *, digest=None, state_dir_path: Path | None = None,
                 replay: EventReplay | None = None) -> None:
        super().__init__(id=f"pane-{handle}")
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self._digest = digest
        self._created_at: str = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.unseen = False
        self._core = AgentSession(session, agent, agent_slug, handle)
        self._core.add_event_observer(self._on_core_event)
        self._core.add_state_observer(self._on_core_state)
        self._core.add_inbox_observer(self._on_core_inbox)
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

    def compose(self) -> ComposeResult:
        with Vertical():
            yield VerticalScroll(id="transcript")
            if self._digest is not None:
                yield QueueStrip(self._digest, self._palette)
            yield StatusBar(self.handle, self.agent_slug,
                            self._agent.model,
                            self._agent.permission.value, self._palette)
            yield GrowingInput(placeholder="type a message…")

    async def on_mount(self) -> None:
        self.query_one(StatusBar).set_state(AgentState.ready)
        self._mount_replay()
        self.refresh_metrics()

    def _mount_replay(self) -> None:
        """Paint prior events from a replay onto the transcript, then
        mark an interrupted turn if the session ended mid-turn."""
        if self._replay is None:
            return
        for ev in self._replay.events:
            self._on_core_event(None, ev)
        if self._replay.interrupted:
            self._flush_streaming()
            self._mount_block(
                Text("⚠ interrupted", style="yellow"),
                "⚠ interrupted")

    def refresh_metrics(self) -> None:
        self.query_one(StatusBar).set_metrics(
            self._core.metrics.render(time.monotonic()))

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _working_indicator(self) -> WorkingIndicator | None:
        matches = self.query(WorkingIndicator)
        return matches.first() if len(matches) else None

    def _mount_block(self, renderable: RenderableType,
                     text_payload: str,
                     *, tight: bool = False) -> CopyableBlock:
        block = CopyableBlock(renderable, text_payload, tight=tight)
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
        t.scroll_end(animate=False)
        return block

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

    async def on_growing_input_submitted(self,
                                  event: GrowingInput.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if not text or self.state is AgentState.working:
            return
        inp = self.query_one(GrowingInput)
        inp.value = ""
        self._submit(text)

    def _submit(self, text: str) -> None:
        self._flush_streaming()
        inp = self.query_one(GrowingInput)
        inp.disabled = True
        width = self._transcript().size.width or 80
        self._mount_block(
            render_user_line(text, self._palette, width), text)
        self._start_indicator()
        self.run_worker(self._core.send(text),
                        group="turn", exclusive=True)

    async def deliver_handoff(self, from_handle: str,
                              context: str) -> None:
        self._submit(f"[handoff from {from_handle}] {context}")

    # --- streaming aggregation -------------------------------------

    def _flush_streaming(self) -> None:
        self._streaming_block = None
        self._streaming_kind = None
        self._streaming_text = ""

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
        else:
            self._streaming_text += new_text
            if self._streaming_block is not None:
                r = self._render_for_stream(
                    kind, self._streaming_text)
                self._streaming_block.update_content(
                    r, self._streaming_text)

    # --- event handlers --------------------------------------------

    def _on_core_inbox(self, _core, msg) -> None:
        """Render an incoming inbox message (handoff / queue callback /
        telegram) as a distinct block in the transcript before the agent
        reacts. Fires on every deliver(), whether the session was idle
        or buffering mid-turn."""
        self._flush_streaming()
        renderable = render_inbox_block(msg, self._palette)
        # Plain-text clipboard payload mirrors the substrate header
        # convention so copy-on-click gives the same shape the agent saw.
        from aegis.queue.schema import render_inbox_header
        payload = f"{render_inbox_header(msg)}\n{msg.body or ''}"
        self._mount_block(renderable, payload)

    def _on_core_event(self, _core, ev) -> None:
        if isinstance(ev, AssistantText):
            if ev.text:
                self._stream_append("text", ev.text)
        elif isinstance(ev, AssistantThinking):
            self._stream_append("thinking", ev.text or "")
        else:
            self._flush_streaming()
            renderable = render_event(ev, self._palette)
            if renderable is not None:
                # ToolUse mounts tight (no margin-bottom) so the
                # following ToolResult sits flush against it — the
                # ⏺ / └ pair reads as one visual unit.
                self._mount_block(
                    renderable, _payload_for_event(ev),
                    tight=isinstance(ev, ToolUse))
        self.refresh_metrics()

    def _on_core_state(self, _core, state: AgentState,
                       finished: bool) -> None:
        self.query_one(StatusBar).set_state(state)
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
            self._stop_indicator()
            inp = self.query_one(GrowingInput)
            inp.disabled = False
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

    async def close(self) -> None:
        await self._core.close()
