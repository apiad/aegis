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
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Label, Select, Static

from aegis.config import ConfigError, load_queues, load_telegram_config
from aegis.config.yaml_loader import load_config as _load_yaml_config
from aegis.tui.state import AgentState


CUSTOM_MODEL_OPTION = "<custom>"


class AddAgentModal(ModalScreen[bool]):
    """Modal: gather slug/provider/model/effort/permission, call
    add_agent on save. Dismisses True on success, False on cancel.

    The model picker is sourced from ``aegis.models.models_for(provider)``
    so the list stays in sync with the YAML registry; picking
    ``<custom>`` reveals a free-form input for an arbitrary model name.
    """

    BINDINGS = [
        Binding("ctrl+s", "save", show=False, priority=True),
        Binding("escape", "cancel", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    AddAgentModal { align: center middle; }
    AddAgentModal #agm-box {
        width: 64; height: auto;
        border: round $panel; background: $surface; padding: 1 2;
    }
    AddAgentModal Label { margin-top: 1; }
    AddAgentModal Input, AddAgentModal Select {
        width: 100%; margin-bottom: 0;
    }
    AddAgentModal #agm-model-custom { display: none; }
    AddAgentModal #agm-model-custom.-visible { display: block; }
    AddAgentModal #agm-err { color: $error; margin-top: 1; height: auto; }
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        with Vertical(id="agm-box"):
            yield Label("Add agent — Ctrl+S save, Esc cancel",
                        markup=False)
            yield Label("slug")
            yield Input(placeholder="e.g. main", id="agm-slug")
            yield Label("provider")
            yield Select(
                [("claude-code", "claude-code"), ("gemini", "gemini"),
                 ("opencode", "opencode")],
                value="claude-code", allow_blank=False, id="agm-provider")
            yield Label("model")
            yield Select(
                _model_options("claude-code"),
                value=_default_model_value("claude-code"),
                allow_blank=False, id="agm-model")
            yield Input(placeholder="custom model name "
                        "(e.g. claude-opus-4-7 or vendor/model)",
                        id="agm-model-custom")
            yield Label("effort (claude-code only)")
            yield Select(
                [("low", "low"), ("medium", "medium"),
                 ("high", "high"), ("max", "max")],
                value="high", allow_blank=False, id="agm-effort")
            yield Label("permission")
            yield Select(
                [("read", "read"), ("write", "write"),
                 ("full", "full"), ("auto", "auto")],
                value="auto", allow_blank=False, id="agm-permission")
            yield Static("", id="agm-err", markup=False)

    def on_mount(self) -> None:
        self.query_one("#agm-slug", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        """When provider changes, repopulate the model Select; when the
        model Select changes, toggle the custom-input row."""
        if event.select.id == "agm-provider":
            provider = str(event.value)
            sel = self.query_one("#agm-model", Select)
            sel.set_options(_model_options(provider))
            sel.value = _default_model_value(provider)
            self._set_custom_visible(sel.value == CUSTOM_MODEL_OPTION)
        elif event.select.id == "agm-model":
            self._set_custom_visible(event.value == CUSTOM_MODEL_OPTION)

    def _set_custom_visible(self, visible: bool) -> None:
        inp = self.query_one("#agm-model-custom", Input)
        if visible:
            inp.add_class("-visible")
            inp.focus()
        else:
            inp.remove_class("-visible")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        slug = self.query_one("#agm-slug", Input).value.strip()
        provider = self.query_one("#agm-provider", Select).value
        model_choice = self.query_one("#agm-model", Select).value
        custom = self.query_one("#agm-model-custom", Input).value.strip()
        effort = self.query_one("#agm-effort", Select).value
        permission = self.query_one("#agm-permission", Select).value
        err = self.query_one("#agm-err", Static)
        if not slug:
            err.update("slug is required")
            return
        model = custom if model_choice == CUSTOM_MODEL_OPTION else str(model_choice)
        if not model:
            err.update("model is required (pick from the list or "
                       "select <custom> and enter a model name)")
            return
        # effort only applies to claude-code.
        effort_arg = effort if provider == "claude-code" else None
        try:
            from aegis.config.edit import add_agent
            add_agent(self._root, slug,
                      provider=str(provider), model=model,
                      effort=effort_arg, permission=str(permission))
        except ConfigError as e:
            err.update(str(e))
            return
        self.dismiss(True)


def _model_options(provider: str) -> list[tuple[str, str]]:
    """Build the Select options for a provider: registry models followed
    by ``<custom>``. The tuple is ``(display_label, value)``; the value
    is what ``Select.value`` returns and is also what gets written to
    ``.aegis.yaml``."""
    from aegis.models import models_for
    out: list[tuple[str, str]] = [
        (label, name) for name, label in models_for(provider)
    ]
    out.append((CUSTOM_MODEL_OPTION, CUSTOM_MODEL_OPTION))
    return out


def _default_model_value(provider: str) -> str:
    """The Select needs an initial value. Use the first registered model
    for the provider, or ``<custom>`` if the provider has no registry
    entries yet."""
    from aegis.models import models_for
    entries = models_for(provider)
    if entries:
        return entries[0][0]
    return CUSTOM_MODEL_OPTION


class ConfigPanel(Widget):
    """A read-only summary of `.aegis.yaml` rendered as stacked panels.

    Quacks like a tab (handle / agent_slug / state / unseen) so the
    TabBar machinery can render it alongside ConversationPane and the
    other tab types.
    """

    BINDINGS = [
        Binding("a", "add_agent", "Add agent", show=False, priority=True),
    ]

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

    # --- keybindings ------------------------------------------------

    @work
    async def action_add_agent(self) -> None:
        """Open AddAgentModal; refresh panel on success."""
        result = await self.app.push_screen_wait(AddAgentModal(self._root))
        if result:
            self.refresh_view()


def _rich_to_text(renderable) -> Text:
    """Render a Rich renderable (Table, Panel, ...) into a Text so we
    can stack it inside the body Static."""
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=110).print(renderable)
    return Text.from_ansi(buf.getvalue())
