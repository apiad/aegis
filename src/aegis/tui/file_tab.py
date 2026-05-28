"""FileTab — TUI tab type for viewing and lightly editing files.

VIEW mode (default): syntax-highlighted read-only display with 2s mtime
polling. File changes on disk trigger an auto-reload.

EDIT mode (press `e`): TextArea becomes writable. File changes on disk
show a warning bar with [r] reload / [k] keep options. Ctrl+S saves.
Esc exits edit mode — if the buffer is dirty, a confirm bar offers
[d] discard / [esc] keep editing instead of exiting silently.

PREVIEW mode (press `p`, .md only): scrollable rendered Markdown via
Textual's MarkdownViewer. Lazy-mounted on first toggle so opening a .md
file stays fast; subsequent toggles just flip visibility. Esc returns
to VIEW.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import MarkdownViewer, Static, TextArea

from aegis.tui.state import AgentState

_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".md": "markdown", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".sh": "bash",
    ".html": "html", ".css": "css", ".rs": "rust",
    ".go": "go", ".c": "c", ".cpp": "cpp", ".rb": "ruby",
}

_MTIME_POLL_S: float = 2.0


class FileTab(Widget, can_focus=True):
    """Viewer/editor tab for a single file."""

    DEFAULT_CSS = """
    FileTab { layout: vertical; height: 1fr; background: $background; }
    FileTab #ft-status { height: 1; background: $panel;
                         color: $foreground; padding: 0 2; }
    FileTab #ft-warn { height: 1; background: $warning; color: $text;
                       padding: 0 2; display: none; }
    FileTab #ft-warn.visible { display: block; }
    FileTab #ft-cancel { height: 1; background: $warning; color: $text;
                         padding: 0 2; display: none; }
    FileTab #ft-cancel.visible { display: block; }
    FileTab TextArea { height: 1fr; }
    FileTab TextArea.hidden { display: none; }
    FileTab MarkdownViewer { height: 1fr; display: none; }
    FileTab MarkdownViewer.visible { display: block; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("p", "preview", "Preview", priority=True),
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
        self._preview_mode: bool = False
        self._md_viewer: MarkdownViewer | None = None
        self._modified: bool = False
        self._mtime: float = 0.0
        self._disk_changed: bool = False
        self._cancel_pending: bool = False

    @property
    def _is_markdown(self) -> bool:
        return self._path.suffix.lower() == ".md"

    # --- compose ----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="ft-status", markup=False)
        yield Static(
            "⚠ file changed on disk — [r] reload (discard edits)  "
            "[k] keep mine",
            id="ft-warn", markup=False)
        yield Static(
            "⚠ unsaved edits — [d] discard  [esc] keep editing",
            id="ft-cancel", markup=False)
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
        if self._edit_mode:
            mode = "EDIT"
        elif self._preview_mode:
            mode = "PREVIEW"
        else:
            mode = "VIEW"
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
            return
        if self._md_viewer is not None:
            with contextlib.suppress(Exception):
                self._md_viewer.document.update(content)

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
        if self._preview_mode:
            self._exit_preview()
        if not self._edit_mode:
            self._edit_mode = True
            self.query_one("#ft-editor", TextArea).read_only = False
            self._refresh_status()

    async def action_preview(self) -> None:
        if not self._is_markdown or self._edit_mode:
            return
        if self._preview_mode:
            self._exit_preview()
        else:
            await self._enter_preview()
        self._refresh_status()

    async def _enter_preview(self) -> None:
        try:
            content = self._path.read_text(errors="replace")
        except OSError:
            return
        if self._md_viewer is None:
            self._md_viewer = MarkdownViewer(
                content, show_table_of_contents=False)
            await self.mount(self._md_viewer)
        else:
            with contextlib.suppress(Exception):
                await self._md_viewer.document.update(content)
        self._preview_mode = True
        self.query_one("#ft-editor", TextArea).add_class("hidden")
        self._md_viewer.add_class("visible")
        # Park focus on FileTab itself first so the next keypress always
        # has a delivery target; the viewer's scroll child may not be
        # ready to accept focus until after the next refresh cycle.
        with contextlib.suppress(Exception):
            self.focus()
        self.call_after_refresh(self._focus_viewer_if_preview)

    def _focus_viewer_if_preview(self) -> None:
        if self._preview_mode and self._md_viewer is not None:
            with contextlib.suppress(Exception):
                self._md_viewer.focus()

    def _exit_preview(self) -> None:
        self._preview_mode = False
        if self._md_viewer is not None:
            self._md_viewer.remove_class("visible")
        self.query_one("#ft-editor", TextArea).remove_class("hidden")

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

    def key_d(self) -> None:
        if self._cancel_pending:
            self._discard_and_exit_edit()

    def key_escape(self) -> None:
        if self._preview_mode and not self._edit_mode:
            self._exit_preview()
            self._refresh_status()
            return
        if not self._edit_mode:
            return
        if self._cancel_pending:
            self._hide_cancel_prompt()
            return
        if self._modified:
            self._show_cancel_prompt()
            return
        self._edit_mode = False
        self.query_one("#ft-editor", TextArea).read_only = True
        self._refresh_status()

    def _show_cancel_prompt(self) -> None:
        self._cancel_pending = True
        # Park the TextArea read-only so `d`/`escape` don't get typed
        # into the buffer while the confirm bar is up.
        with contextlib.suppress(Exception):
            self.query_one("#ft-editor", TextArea).read_only = True
        with contextlib.suppress(Exception):
            self.query_one("#ft-cancel", Static).add_class("visible")

    def _hide_cancel_prompt(self) -> None:
        was_pending = self._cancel_pending
        self._cancel_pending = False
        with contextlib.suppress(Exception):
            self.query_one("#ft-cancel", Static).remove_class("visible")
        if was_pending and self._edit_mode:
            with contextlib.suppress(Exception):
                self.query_one("#ft-editor", TextArea).read_only = False

    def _discard_and_exit_edit(self) -> None:
        self._edit_mode = False
        editor = self.query_one("#ft-editor", TextArea)
        editor.read_only = True
        try:
            content = self._path.read_text(errors="replace")
            self._mtime = self._path.stat().st_mtime
            editor.load_text(content)
        except OSError:
            pass
        self._modified = False
        self._hide_cancel_prompt()
        self._hide_disk_changed_warning()
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
            if self._preview_mode and self._md_viewer is not None:
                self._md_viewer.focus()
            else:
                self.query_one("#ft-editor", TextArea).focus()

    async def close(self) -> None:
        pass
