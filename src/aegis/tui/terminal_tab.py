"""TerminalTab — TUI tab type for live shared PTYs.

Renders a command-session log: each command in the ledger becomes a
block (header / output / footer chip). Live output streams into a
"running" block at the bottom. The input bar has two modes:

  - run (default): Enter submits a full command via terminal_manager.run.
  - raw (Ctrl+K toggles): every keystroke is sent verbatim via send_keys.

The TerminalTab is shaped to coexist with ConversationPane in the
AegisApp tab roster: it exposes ``handle``, ``agent_slug`` (= "term"),
``state`` (AgentState.ready / working), ``unseen``, ``id`` and an
async ``close()``.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from pathlib import Path

from rich.console import RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from aegis.terminal.manager import CommandRecord, TerminalInfo, TerminalManager
from aegis.tui.state import AgentState


class TerminalTabStateChanged(Message):
    """Emitted when the tab's idle/working state flips, so the app can
    repaint its tabbar."""

    def __init__(self, tab: "TerminalTab", finished: bool) -> None:
        self.tab = tab
        self.finished = finished
        super().__init__()


class _Block(Widget):
    """One rendered command block — header + body + footer. Click copies
    the original command to the clipboard."""

    DEFAULT_CSS = """
    _Block { height: auto; padding: 0 1; margin-bottom: 1;
             background: $background; }
    _Block:hover { background: $surface; }
    _Block > .content { background: transparent; height: auto; }
    """

    def __init__(self, renderable: RenderableType, cmd_payload: str) -> None:
        super().__init__()
        self._renderable = renderable
        self._cmd_payload = cmd_payload
        self.tooltip = "click to copy command"

    def compose(self) -> ComposeResult:
        yield Static(self._renderable, classes="content", markup=False)

    def update_content(self, renderable: RenderableType) -> None:
        self._renderable = renderable
        with contextlib.suppress(Exception):
            self.query_one(".content", Static).update(renderable)

    def on_click(self, event: Click) -> None:
        if not self._cmd_payload:
            return
        try:
            self.app.copy_to_clipboard(self._cmd_payload)
            self.app.notify(f"copied {len(self._cmd_payload)} chars",
                            timeout=1.2)
        except Exception:
            pass


def _fmt_record(rec: CommandRecord) -> str:
    exit_str = (f"exit {rec.exit}" if rec.exit is not None
                else ("timed out" if rec.timed_out
                      else ("killed by restart" if rec.killed_by_restart
                            else "exit ?")))
    dur = f"{rec.duration_s:.2f}s" if rec.duration_s else "—"
    head = f"$ {rec.cmd}  · {rec.writer}  · {rec.started_at}"
    body = (rec.stdout or "").rstrip("\n")
    if rec.stderr:
        body = (body + ("\n" if body else "")
                + "── stderr ──\n" + rec.stderr.rstrip("\n"))
    foot = f"↳ {exit_str} · {dur}"
    return f"{head}\n{body}\n{foot}" if body else f"{head}\n{foot}"


def _fmt_running(cmd: str, writer: str, started_at: str, stdout: str) -> str:
    body = stdout.rstrip("\n")
    head = f"$ {cmd}  · {writer}  · {started_at}"
    return f"{head}\n{body}\n↳ running…" if body else f"{head}\n↳ running…"


class TerminalTab(Widget):
    DEFAULT_CSS = """
    TerminalTab { layout: vertical; height: 1fr;
                  background: $background; }
    TerminalTab #term-transcript { height: 1fr; background: $background;
                                   padding: 1 4; scrollbar-size: 0 0; }
    TerminalTab .status-strip { height: 1; background: $panel;
                                color: $foreground; padding: 0 2; }
    TerminalTab .mode-strip { height: 1; background: $surface;
                              color: $foreground; padding: 0 2; }
    TerminalTab Input { height: 3; background: $surface;
                        color: $foreground; padding: 0 2; border: none;
                        border-top: solid $foreground 20%;
                        border-bottom: solid $foreground 20%; }
    TerminalTab .dim { color: $foreground 50%; }
    """

    BINDINGS = [
        Binding("ctrl+k", "toggle_mode", "Toggle raw/run", priority=False),
    ]

    def __init__(self, manager: TerminalManager, info: TerminalInfo,
                 *, palette=None) -> None:
        super().__init__(id=f"term-{info.name}")
        self._manager = manager
        self._info = info
        self.handle = info.name
        self.agent_slug = "term"
        self.unseen = False
        self.state = AgentState.ready
        self._mode = "run"  # or "raw"
        self._created_at: str = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self._palette = palette
        self._running_block: _Block | None = None
        self._running_cmd: str | None = None
        self._running_writer: str | None = None
        self._running_started_at: str | None = None
        self._running_stdout: str = ""

    # --- compose ----------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield VerticalScroll(id="term-transcript")
            yield Static("", classes="status-strip", id="term-status",
                         markup=False)
            yield Static("[run] · Ctrl+K → raw mode",
                         classes="mode-strip", id="term-mode",
                         markup=False)
            yield Input(placeholder="type a command and press Enter…",
                        id="term-input")

    async def on_mount(self) -> None:
        self._refresh_status()
        self._mount_past_ledger()
        self._manager.add_render_observer(self.handle, self._on_render_event)

    def _mount_past_ledger(self) -> None:
        try:
            recs = self._manager.read(self.handle, last_n=200)
        except Exception:
            recs = []
        if not recs:
            return
        t = self._transcript()
        for rec in recs:
            block = _Block(Text(_fmt_record(rec), style="dim"), rec.cmd)
            block.add_class("dim")
            t.mount(block)
        # Visual separator between past session and live.
        t.mount(Static("── live ──", classes="dim", markup=False))
        t.scroll_end(animate=False)

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#term-transcript", VerticalScroll)

    def _refresh_status(self) -> None:
        info = self._info
        try:
            sub_n = len(self._manager.subscribers(info.name))
        except Exception:
            sub_n = 0
        exit_str = (f"last exit {info.last_exit}"
                    if info.last_exit is not None else "no commands yet")
        text = (f"{info.cwd} · pid {info.pid} · {info.shell} · "
                f"{exit_str} · subs: {sub_n}")
        with contextlib.suppress(Exception):
            self.query_one("#term-status", Static).update(text)

    def _refresh_mode_strip(self) -> None:
        label = ("[raw] · Ctrl+K → run mode" if self._mode == "raw"
                 else "[run] · Ctrl+K → raw mode")
        with contextlib.suppress(Exception):
            self.query_one("#term-mode", Static).update(label)

    # --- render-observer callback -----------------------------------

    def _on_render_event(self, kind: str, payload: dict) -> None:
        if kind == "chunk":
            data: bytes = payload.get("data", b"")
            if self._running_block is None:
                return
            chunk_text = data.decode("utf-8", errors="replace")
            self._running_stdout += chunk_text
            self._update_running_block()
        elif kind == "command_end":
            rec: CommandRecord = payload["record"]
            self._finalize_running(rec)
            self._info.last_exit = rec.exit
            self._refresh_status()

    def _update_running_block(self) -> None:
        if self._running_block is None or self._running_cmd is None:
            return
        text = _fmt_running(
            self._running_cmd, self._running_writer or "?",
            self._running_started_at or "", self._running_stdout)
        self._running_block.update_content(Text(text))
        with contextlib.suppress(Exception):
            self._transcript().scroll_end(animate=False)

    def _finalize_running(self, rec: CommandRecord) -> None:
        block = self._running_block
        self._running_block = None
        self._running_cmd = None
        self._running_writer = None
        self._running_started_at = None
        self._running_stdout = ""
        renderable = Text(_fmt_record(rec))
        if block is not None:
            block.update_content(renderable)
        else:
            t = self._transcript()
            t.mount(_Block(renderable, rec.cmd))
            t.scroll_end(animate=False)
        if self.state is AgentState.working:
            self.state = AgentState.ready
            self.post_message(TerminalTabStateChanged(self, finished=True))
        self.unseen = True

    # --- input handling ---------------------------------------------

    def action_toggle_mode(self) -> None:
        self._mode = "raw" if self._mode == "run" else "run"
        self._refresh_mode_strip()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value
        inp = self.query_one(Input)
        inp.value = ""
        if self._mode == "raw":
            await self._manager.send_keys(self.handle, text + "\n",
                                          writer="human")
            return
        cmd = text.strip()
        if not cmd:
            return
        self._begin_running(cmd, "human")
        self.run_worker(self._run_cmd(cmd), group="term-run", exclusive=False)

    def _begin_running(self, cmd: str, writer: str) -> None:
        self._running_cmd = cmd
        self._running_writer = writer
        self._running_started_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))
        self._running_stdout = ""
        block = _Block(
            Text(_fmt_running(cmd, writer, self._running_started_at, "")),
            cmd)
        t = self._transcript()
        t.mount(block)
        t.scroll_end(animate=False)
        self._running_block = block
        self.state = AgentState.working
        self.post_message(TerminalTabStateChanged(self, finished=False))

    async def _run_cmd(self, cmd: str) -> None:
        try:
            await self._manager.run(self.handle, cmd, writer="human")
        except Exception as e:
            err = Text(f"⚠ run failed: {e}", style="red")
            t = self._transcript()
            t.mount(Static(err, markup=False))
            t.scroll_end(animate=False)
            self.state = AgentState.error
            self.post_message(TerminalTabStateChanged(self, finished=True))

    def focus_input(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(Input).focus()

    # --- AppBridge-compatible lifecycle -----------------------------

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            self._manager.remove_render_observer(
                self.handle, self._on_render_event)
        with contextlib.suppress(Exception):
            await self._manager.close(self.handle)
