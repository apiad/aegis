"""ConfigPanel — TUI tab for inspecting and editing .aegis.yaml.

Slice 6: read-only render. Slice 7 adds add/remove modals. Slice 8
wires reload-on-disk-change so a side-terminal `aegis config` write
reflects here in real time.
"""
from __future__ import annotations

import contextlib
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from aegis.config import ConfigError, load_queues, load_telegram_config
from aegis.config.yaml_loader import load_config as _load_yaml_config
from aegis.tui.state import AgentState


class ConfigPanel(Widget):
    """A read-only summary of `.aegis.yaml` rendered as stacked panels.

    Quacks like a tab (handle / agent_slug / state / unseen) so the
    TabBar machinery can render it alongside ConversationPane and the
    other tab types.
    """

    DEFAULT_CSS = """
    ConfigPanel { layout: vertical; height: 1fr; background: $background; }
    ConfigPanel #cp-scroll { height: 1fr; padding: 1 2; }
    ConfigPanel #cp-body { background: $background; height: auto; }
    """

    def __init__(self, root: Path) -> None:
        super().__init__(id="config-panel")
        self._root = root.resolve()
        self.handle: str = "config"
        self.agent_slug: str = "config"
        self.state: AgentState = AgentState.ready
        self.unseen: bool = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="cp-scroll"):
            yield Static("", id="cp-body", markup=False)

    async def on_mount(self) -> None:
        self.refresh_view()

    # --- rendering --------------------------------------------------

    def _build_renderable(self) -> Text:
        """Return the full panel body as a Rich Text."""
        out = Text()
        base = self._root / ".aegis.yaml"

        if not base.is_file():
            out.append(
                "no .aegis.yaml at this root.\n\n"
                f"path: {base}\n\n"
                "Add an agent to get started:\n"
                "    aegis config agent add main --provider claude-code "
                "--model opus --effort high\n\n"
                "or use the [+ Add agent] button below (slice 7).",
                style="dim")
            return out

        try:
            cfg = _load_yaml_config(self._root)
        except ConfigError as e:
            out.append(f"⚠ .aegis.yaml does not parse: {e}\n",
                       style="bold red")
            return out

        out.append(self._render_summary_line(cfg))
        out.append("\n\n")
        out.append(self._render_agents_panel(cfg))
        out.append("\n\n")
        out.append(self._render_queues_panel())
        out.append("\n\n")
        out.append(self._render_telegram_panel())
        out.append("\n\n")
        out.append(self._render_plugin_dirs_panel(cfg))
        return out

    def _render_summary_line(self, cfg) -> Text:
        t = Text()
        t.append("default agent: ", style="bold")
        t.append(str(cfg.default_agent or "—"))
        return t

    def _render_agents_panel(self, cfg) -> Text:
        if not cfg.agents:
            return Text("AGENTS\n  (none — run `aegis config agent add …`)",
                        style="dim")
        table = Table(title="AGENTS", title_justify="left",
                      title_style="bold",
                      show_header=True, header_style="bold")
        table.add_column("slug")
        table.add_column("provider")
        table.add_column("model")
        table.add_column("effort")
        table.add_column("permission")
        table.add_column("default")
        for name, a in cfg.agents.items():
            is_default = "✓" if name == cfg.default_agent else ""
            effort = a.effort.value if a.harness == "claude-code" else "—"
            table.add_row(name, a.harness, a.model, effort,
                          a.permission.value, is_default)
        return _rich_to_text(table)

    def _render_queues_panel(self) -> Text:
        try:
            queues = load_queues(self._root)
        except ConfigError as e:
            return Text(f"QUEUES\n  ⚠ {e}", style="red")
        if not queues:
            return Text("QUEUES\n  (none)", style="dim")
        table = Table(title="QUEUES", title_justify="left",
                      title_style="bold",
                      show_header=True, header_style="bold")
        table.add_column("name")
        table.add_column("agent")
        table.add_column("max_parallel", justify="right")
        table.add_column("budgets")
        for name, q in queues.items():
            if q.budgets:
                budgets = ", ".join(
                    f"{b.constraint}:{b.limit}/{b.window_str}"
                    for b in q.budgets)
            else:
                budgets = "—"
            table.add_row(name, q.agent_profile,
                          str(q.max_parallel), budgets)
        return _rich_to_text(table)

    def _render_telegram_panel(self) -> Text:
        try:
            tcfg = load_telegram_config(self._root)
        except ConfigError:
            return Text("TELEGRAM\n  (none)", style="dim")
        if (tcfg.token is None and tcfg.chat_id is None
                and not tcfg.auto_prompt):
            return Text("TELEGRAM\n  (none)", style="dim")
        t = Text()
        t.append("TELEGRAM\n", style="bold")
        if tcfg.token:
            redacted = (tcfg.token[:4] + "…"
                        + f"({len(tcfg.token)}ch)")
            t.append(f"  token:       {redacted}\n")
        else:
            t.append("  token:       —\n", style="dim")
        t.append(f"  chat_id:     {tcfg.chat_id or '—'}\n")
        if tcfg.auto_prompt:
            preview = (tcfg.auto_prompt[:60] + "…"
                       if len(tcfg.auto_prompt) > 60 else tcfg.auto_prompt)
            t.append(f"  auto_prompt: {preview!r}\n")
        return t

    def _render_plugin_dirs_panel(self, cfg) -> Text:
        t = Text()
        t.append("PLUGIN DIRS\n", style="bold")
        if not cfg.plugin_dirs:
            t.append("  (none)\n", style="dim")
            return t
        for p in cfg.plugin_dirs:
            try:
                rel = p.relative_to(self._root)
                t.append(f"  {rel}\n")
            except ValueError:
                t.append(f"  {p}\n")
        return t

    def refresh_view(self) -> None:
        """Re-read `.aegis.yaml` and repaint."""
        body = self._build_renderable()
        with contextlib.suppress(Exception):
            self.query_one("#cp-body", Static).update(body)

    def rendered_text(self) -> str:
        """Test helper: render the panel body to a flat string."""
        body = self._build_renderable()
        buf = StringIO()
        Console(file=buf, force_terminal=False, width=120).print(body)
        return buf.getvalue()

    # --- tab plumbing -----------------------------------------------

    def focus_input(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#cp-scroll").focus()

    async def close(self) -> None:
        pass


def _rich_to_text(renderable) -> Text:
    """Render a Rich renderable (Table, Panel, ...) into a Text so we
    can stack it inside the body Static."""
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=110).print(renderable)
    return Text.from_ansi(buf.getvalue())
