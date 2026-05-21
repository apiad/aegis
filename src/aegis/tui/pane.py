from __future__ import annotations

import contextlib
import random
import time

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.drivers.base import HarnessSession
from aegis.events import AssistantText, AssistantThinking
from aegis.render import render_event, render_user_line
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar


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
    CopyableBlock:hover { background: $surface; }
    CopyableBlock > .content { background: transparent; height: auto; }
    /* Hint is hidden by default so blocks stay slim. Hovering the
       block reveals it underneath; the 1-row layout shift is only
       there for the duration of the hover. */
    CopyableBlock > .copy-hint { display: none; height: 1;
                                 color: $foreground 30%;
                                 text-style: italic; text-align: right;
                                 background: transparent; }
    CopyableBlock:hover > .copy-hint { display: block; }
    """

    def __init__(self, renderable: RenderableType,
                 text_payload: str) -> None:
        super().__init__()
        self._renderable = renderable
        self._text_payload = text_payload

    def compose(self) -> ComposeResult:
        yield Static(self._renderable, classes="content")
        yield Static("(click to copy)", classes="copy-hint")

    def update_content(self, renderable: RenderableType,
                       text_payload: str) -> None:
        self._renderable = renderable
        self._text_payload = text_payload
        with contextlib.suppress(Exception):
            self.query_one(".content", Static).update(renderable)

    def text_payload(self) -> str:
        return self._text_payload

    def on_click(self, event: Click) -> None:
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
    ConversationPane Input { height: 3; background: $surface;
                             color: $foreground; padding: 0 2;
                             border: none;
                             border-top: solid $foreground 20%;
                             border-bottom: solid $foreground 20%;
                             margin-top: 1; }
    ConversationPane Input:focus { border: none;
                             border-top: solid $foreground 20%;
                             border-bottom: solid $foreground 20%; }
    """

    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_slug: str, handle: str, palette) -> None:
        super().__init__(id=f"pane-{handle}")
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self.unseen = False
        self._core = AgentSession(session, agent, agent_slug, handle)
        self._core.on_event = self._on_core_event
        self._core.on_state = self._on_core_state
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

    def compose(self) -> ComposeResult:
        with Vertical():
            yield VerticalScroll(id="transcript")
            yield StatusBar(self.handle, self.agent_slug,
                            self._agent.model,
                            self._agent.permission.value, self._palette)
            yield Input(placeholder="type a message…")

    async def on_mount(self) -> None:
        self.query_one(StatusBar).set_state(AgentState.ready)
        self.refresh_metrics()

    def refresh_metrics(self) -> None:
        self.query_one(StatusBar).set_metrics(
            self._core.metrics.render(time.monotonic()))

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _working_indicator(self) -> WorkingIndicator | None:
        matches = self.query(WorkingIndicator)
        return matches.first() if len(matches) else None

    def _mount_block(self, renderable: RenderableType,
                     text_payload: str) -> CopyableBlock:
        block = CopyableBlock(renderable, text_payload)
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
        self.query_one(Input).focus()

    async def on_input_submitted(self,
                                  event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if not text or self.state is AgentState.working:
            return
        inp = self.query_one(Input)
        inp.value = ""
        self._submit(text)

    def _submit(self, text: str) -> None:
        self._flush_streaming()
        inp = self.query_one(Input)
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
            return Text("✻ Thinking…", style=self._palette.muted)
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
                self._mount_block(renderable, _payload_for_event(ev))
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
            inp = self.query_one(Input)
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
            inp = self.query_one(Input)
            inp.disabled = False
            inp.focus()

        self.run_worker(_do(), group="turn", exclusive=True)

    async def close(self) -> None:
        await self._core.close()
