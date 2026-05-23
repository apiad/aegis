"""TerminalTab — TUI tab type for live shared PTYs.

Each command in the terminal renders as a block with three regions:

  • _CmdHeader  — the `$ <cmd>` line, click-to-copy the raw command.
  • _OutputBox  — the captured stdout/stderr, click-to-copy the output.
  • _FooterChip — exit code + duration when finished, or a live timer
                  (`↳ running… 4.2s`) while in flight.

Live output streams into the running block's _OutputBox via the
terminal manager's render observer; a `set_interval` tick refreshes
the _FooterChip's elapsed time even when the command produces no
output for stretches.

The input bar has two modes:

  - run (default): Enter submits a full command via TerminalManager.run.
  - raw (Ctrl+K toggles): every keystroke goes to send_keys.

The TerminalTab is shaped to coexist with ConversationPane in the
AegisApp tab roster: it exposes ``handle``, ``agent_slug`` (= "term"),
``state``, ``unseen``, ``id`` and an async ``close()``.
"""
from __future__ import annotations

import contextlib
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static
from textual.timer import Timer

from aegis.terminal.manager import CommandRecord, TerminalInfo, TerminalManager
from aegis.tui.state import AgentState


class TerminalTabStateChanged(Message):
    """Emitted when the tab's idle/working state flips, so the app can
    repaint its tabbar."""

    def __init__(self, tab: "TerminalTab", finished: bool) -> None:
        self.tab = tab
        self.finished = finished
        super().__init__()


# --- Per-region widgets ---------------------------------------------


class _CmdHeader(Static):
    """The `$ <cmd>` line. Click copies the raw command."""

    DEFAULT_CSS = """
    _CmdHeader { height: auto; padding: 0 0; }
    _CmdHeader:hover { background: $surface; }
    """

    def __init__(self, cmd: str, writer: str, started_at: str) -> None:
        super().__init__(self._build_text(cmd, writer, started_at),
                         markup=False)
        self._cmd = cmd
        self.tooltip = "click to copy command"

    @staticmethod
    def _build_text(cmd: str, writer: str, started_at: str) -> Text:
        t = Text(no_wrap=False, overflow="fold")
        t.append("$ ", style="bold yellow")
        t.append(cmd, style="bold")
        t.append("    ", style="dim")
        t.append(f"{writer}", style="dim")
        t.append(f"  ·  {started_at}", style="dim")
        return t

    def on_click(self, event: Click) -> None:
        event.stop()
        try:
            self.app.copy_to_clipboard(self._cmd)
            self.app.notify(f"copied command ({len(self._cmd)} chars)",
                            timeout=1.2)
        except Exception:
            pass


class _OutputBox(Static):
    """Captured stdout (+ stderr block when present). Click copies the
    full raw output."""

    DEFAULT_CSS = """
    _OutputBox { height: auto; padding: 0 0 0 2;
                 border-left: solid $foreground 20%;
                 color: $foreground 80%; }
    _OutputBox:hover { background: $surface; }
    _OutputBox.empty { display: none; }
    """

    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self._stdout = stdout
        self._stderr = stderr
        body = self._compose_body(stdout, stderr)
        super().__init__(body, markup=False)
        self.tooltip = "click to copy output"
        if not body:
            self.add_class("empty")

    @staticmethod
    def _compose_body(stdout: str, stderr: str) -> str:
        body = (stdout or "").rstrip("\n")
        if stderr:
            body = (body + ("\n" if body else "")
                    + "── stderr ──\n" + stderr.rstrip("\n"))
        return body

    def set_text(self, stdout: str, stderr: str = "") -> None:
        """Post-mount update — safe to call only after the widget is
        attached to the running App."""
        self._stdout = stdout
        self._stderr = stderr
        body = self._compose_body(stdout, stderr)
        if body:
            self.remove_class("empty")
            self.update(body)
        else:
            self.add_class("empty")
            self.update("")

    def on_click(self, event: Click) -> None:
        event.stop()
        payload = self._stdout
        if self._stderr:
            payload = (payload + ("\n" if payload else "")
                       + "── stderr ──\n" + self._stderr)
        if not payload:
            return
        try:
            self.app.copy_to_clipboard(payload)
            self.app.notify(f"copied output ({len(payload)} chars)",
                            timeout=1.2)
        except Exception:
            pass


class _FooterChip(Static):
    """Bottom chip: `↳ exit N · 4.20s` or live `↳ running… 4.2s`."""

    DEFAULT_CSS = """
    _FooterChip { height: 1; padding: 0 0; }
    """

    def __init__(self, initial: Text | None = None) -> None:
        super().__init__(initial or Text("↳ pending", style="dim"),
                         markup=False)

    @staticmethod
    def _running_text(elapsed_s: float) -> Text:
        t = Text()
        t.append("↳ ", style="dim")
        t.append("running… ", style="bold yellow")
        t.append(f"{elapsed_s:.1f}s", style="bold yellow")
        return t

    @staticmethod
    def _finished_text(exit_code: int | None, duration_s: float | None,
                       *, timed_out: bool = False,
                       killed_by_restart: bool = False) -> Text:
        t = Text()
        t.append("↳ ", style="dim")
        if killed_by_restart:
            t.append("killed by restart", style="bold red")
        elif timed_out:
            t.append("timed out", style="bold yellow")
        elif exit_code is None:
            t.append("exit ?", style="bold red")
        elif exit_code == 0:
            t.append(f"exit {exit_code}", style="bold green")
        else:
            t.append(f"exit {exit_code}", style="bold red")
        dur = f"{duration_s:.2f}s" if duration_s else "—"
        t.append(f"  ·  {dur}", style="dim")
        return t

    def set_running(self, elapsed_s: float) -> None:
        """Post-mount update."""
        self.update(self._running_text(elapsed_s))

    def set_finished(self, exit_code: int | None, duration_s: float | None,
                     *, timed_out: bool = False,
                     killed_by_restart: bool = False) -> None:
        """Post-mount update."""
        self.update(self._finished_text(
            exit_code, duration_s,
            timed_out=timed_out, killed_by_restart=killed_by_restart,
        ))


class _Block(Widget):
    """One command block: header + output + footer.

    Holds three widgets, each with its own click region. The block as
    a whole owns no click handler — children stop propagation.

    Initial state is baked into the children's constructors so that
    rendering a block in a fully-finalized state requires no post-mount
    .update() calls (which would fail if invoked synchronously after
    `t.mount(block)` — the children aren't attached to the app yet).
    """

    DEFAULT_CSS = """
    _Block { height: auto; padding: 0 1; margin-bottom: 1;
             background: $background; }
    _Block.past { color: $foreground 50%; }
    """

    def __init__(self, cmd: str, writer: str, started_at: str,
                 *, stdout: str = "", stderr: str = "",
                 footer: Text | None = None) -> None:
        super().__init__()
        self._header = _CmdHeader(cmd, writer, started_at)
        self._output = _OutputBox(stdout, stderr)
        self._footer = _FooterChip(footer)

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._output
        yield self._footer

    @property
    def header(self) -> _CmdHeader:
        return self._header

    @property
    def output(self) -> _OutputBox:
        return self._output

    @property
    def footer(self) -> _FooterChip:
        return self._footer

    @classmethod
    def running(cls, cmd: str, writer: str, started_at: str) -> "_Block":
        return cls(cmd, writer, started_at,
                   footer=_FooterChip._running_text(0.0))

    @classmethod
    def finished(cls, rec: CommandRecord) -> "_Block":
        return cls(
            rec.cmd, rec.writer, rec.started_at,
            stdout=rec.stdout or "", stderr=rec.stderr or "",
            footer=_FooterChip._finished_text(
                rec.exit, rec.duration_s,
                timed_out=rec.timed_out,
                killed_by_restart=rec.killed_by_restart,
            ),
        )


# --- Tab -------------------------------------------------------------


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

    # Live timer refresh interval for the running footer chip.
    _TIMER_INTERVAL_S: float = 0.5

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
        self._running_started_monotonic: float | None = None
        self._running_stdout: str = ""
        self._timer: Timer | None = None

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
            block = _Block.finished(rec)
            block.add_class("past")
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
            self._running_block.output.set_text(self._running_stdout)
            with contextlib.suppress(Exception):
                self._transcript().scroll_end(animate=False)
        elif kind == "command_end":
            rec: CommandRecord = payload["record"]
            self._finalize_running(rec)
            self._info.last_exit = rec.exit
            self._refresh_status()

    def _tick_running_footer(self) -> None:
        block = self._running_block
        if block is None or self._running_started_monotonic is None:
            return
        elapsed = time.monotonic() - self._running_started_monotonic
        block.footer.set_running(elapsed)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            with contextlib.suppress(Exception):
                self._timer.stop()
            self._timer = None

    def _finalize_running(self, rec: CommandRecord) -> None:
        block = self._running_block
        self._stop_timer()
        self._running_block = None
        self._running_started_monotonic = None
        self._running_stdout = ""
        if block is not None:
            block.output.set_text(rec.stdout or "", rec.stderr or "")
            block.footer.set_finished(
                rec.exit, rec.duration_s,
                timed_out=rec.timed_out,
                killed_by_restart=rec.killed_by_restart,
            )
        else:
            # Race: command finished before we mounted a running block.
            # Render a fresh, fully-finalized block — state is baked into
            # the children's constructors so no post-mount .update() is
            # needed (which would fail before the next event-loop tick).
            t = self._transcript()
            t.mount(_Block.finished(rec))
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
        started_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))
        self._running_started_monotonic = time.monotonic()
        self._running_stdout = ""
        block = _Block.running(cmd, writer, started_at)
        t = self._transcript()
        t.mount(block)
        t.scroll_end(animate=False)
        self._running_block = block
        self.state = AgentState.working
        self.post_message(TerminalTabStateChanged(self, finished=False))
        # Start the live elapsed-time ticker.
        self._stop_timer()
        self._timer = self.set_interval(
            self._TIMER_INTERVAL_S, self._tick_running_footer)

    async def _run_cmd(self, cmd: str) -> None:
        try:
            await self._manager.run(self.handle, cmd, writer="human")
        except Exception as e:
            self._stop_timer()
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
        self._stop_timer()
        with contextlib.suppress(Exception):
            self._manager.remove_render_observer(
                self.handle, self._on_render_event)
        with contextlib.suppress(Exception):
            await self._manager.close(self.handle)
