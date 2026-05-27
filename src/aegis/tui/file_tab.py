"""FileTab — TUI tab type for viewing and lightly editing files.

VIEW mode (default): syntax-highlighted read-only display with 2s mtime
polling. File changes on disk trigger an auto-reload.

EDIT mode (press `e`): TextArea becomes writable. File changes on disk
show a warning bar with [r] reload / [k] keep options. Ctrl+S saves.
Esc exits edit mode (leaving modifications in memory until saved or the
tab is closed).
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Static, TextArea

from aegis.tui.state import AgentState

_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".md": "markdown", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".sh": "bash",
    ".html": "html", ".css": "css", ".rs": "rust",
    ".go": "go", ".c": "c", ".cpp": "cpp", ".rb": "ruby",
}

_MTIME_POLL_S: float = 2.0


class FileTab(Widget):
    """Viewer/editor tab for a single file."""

    DEFAULT_CSS = """
    FileTab { layout: vertical; height: 1fr; background: $background; }
    FileTab #ft-status { height: 1; background: $panel;
                         color: $foreground; padding: 0 2; }
    FileTab #ft-warn { height: 1; background: $warning; color: $text;
                       padding: 0 2; display: none; }
    FileTab #ft-warn.visible { display: block; }
    FileTab TextArea { height: 1fr; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        # Stable id derived from path so duplicate opens are detected.
        tab_id = f"filetab-{abs(hash(str(self._path)))}"
        super().__init__(id=tab_id)
        self.handle: str = f"file:{self._path.name}"
        self.agent_slug: str = "file"
        self.state: AgentState = AgentState.ready
        self.unseen: bool = False
        self._edit_mode: bool = False
        self._modified: bool = False
        self._mtime: float = 0.0
        self._disk_changed: bool = False

    # --- compose ----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="ft-status", markup=False)
        yield Static(
            "⚠ file changed on disk — [r] reload (discard edits)  "
            "[k] keep mine",
            id="ft-warn", markup=False)
        lang = _EXT_LANGUAGE.get(self._path.suffix.lower())
        yield TextArea(language=lang, read_only=True, id="ft-editor")

    async def on_mount(self) -> None:
        try:
            content = self._path.read_text(errors="replace")
            self._mtime = self._path.stat().st_mtime
        except OSError:
            content = f"(could not read {self._path})"
        self.query_one("#ft-editor", TextArea).load_text(content)
        self._refresh_status()
        self.set_interval(_MTIME_POLL_S, self._check_mtime)

    # --- status bar -------------------------------------------------

    def _refresh_status(self) -> None:
        mode = "EDIT" if self._edit_mode else "VIEW"
        mod = " *" if self._modified else ""
        try:
            loc = self.query_one("#ft-editor", TextArea).cursor_location
            pos = f"  {loc[0] + 1}:{loc[1] + 1}"
        except Exception:
            pos = ""
        text = f"{self._path}    [{mode}]{mod}{pos}"
        with contextlib.suppress(Exception):
            self.query_one("#ft-status", Static).update(text)

    # --- mtime polling ----------------------------------------------

    def _check_mtime(self) -> None:
        try:
            new_mtime = self._path.stat().st_mtime
        except OSError:
            return
        if new_mtime <= self._mtime:
            return
        self._mtime = new_mtime
        if not self._edit_mode:
            self._reload_silent()
        else:
            self._show_disk_changed_warning()

    def _reload_silent(self) -> None:
        try:
            content = self._path.read_text(errors="replace")
            self.query_one("#ft-editor", TextArea).load_text(content)
        except OSError:
            pass

    def _show_disk_changed_warning(self) -> None:
        self._disk_changed = True
        with contextlib.suppress(Exception):
            self.query_one("#ft-warn", Static).add_class("visible")

    def _hide_disk_changed_warning(self) -> None:
        self._disk_changed = False
        with contextlib.suppress(Exception):
            self.query_one("#ft-warn", Static).remove_class("visible")

    # --- keybindings ------------------------------------------------

    def key_e(self) -> None:
        if not self._edit_mode:
            self._edit_mode = True
            self.query_one("#ft-editor", TextArea).read_only = False
            self._refresh_status()

    def key_r(self) -> None:
        if self._disk_changed:
            try:
                content = self._path.read_text(errors="replace")
                self._mtime = self._path.stat().st_mtime
                self.query_one("#ft-editor", TextArea).load_text(content)
                self._modified = False
            except OSError:
                pass
            self._hide_disk_changed_warning()
            self._refresh_status()

    def key_k(self) -> None:
        if self._disk_changed:
            self._hide_disk_changed_warning()

    def key_escape(self) -> None:
        if self._edit_mode:
            self._edit_mode = False
            self.query_one("#ft-editor", TextArea).read_only = True
            self._refresh_status()

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        if self._edit_mode:
            self._modified = True
            self._refresh_status()

    async def action_save(self) -> None:
        if not self._edit_mode:
            return
        try:
            content = self.query_one("#ft-editor", TextArea).text
            self._path.write_text(content)
            self._mtime = self._path.stat().st_mtime
            self._modified = False
            with contextlib.suppress(Exception):
                self.app.notify(f"saved {self._path.name}", timeout=1.5)
        except OSError as e:
            with contextlib.suppress(Exception):
                self.app.notify(f"save failed: {e}",
                                severity="error", timeout=3.0)
        self._refresh_status()

    # --- AppBridge-compatible interface ----------------------------

    def focus_input(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#ft-editor", TextArea).focus()

    async def close(self) -> None:
        pass
