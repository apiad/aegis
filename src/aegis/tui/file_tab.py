"""FileTab — TUI tab type for viewing and lightly editing files.

VIEW mode (default): syntax-highlighted read-only display with 2s mtime
polling. File changes on disk trigger an auto-reload.

EDIT mode (press `e`): TextArea becomes writable. File changes on disk
show a warning bar with [r] reload / [k] keep options. Ctrl+S saves.
Esc exits edit mode — if the buffer is dirty, a confirm bar offers
[d] discard / [esc] keep editing instead of exiting silently.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Markdown, Static, TextArea

from aegis.tui.state import AgentState

_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".md": "markdown", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".sh": "bash",
    ".html": "html", ".css": "css", ".rs": "rust",
    ".go": "go", ".c": "c", ".cpp": "cpp", ".rb": "ruby",
}

_MTIME_POLL_S: float = 2.0


class FocusableMarkdown(Markdown, can_focus=True):
    """Markdown widget that can be focused for keyboard scrolling."""


class FileTab(Widget):
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
    FileTab #ft-markdown { height: 1fr; display: none; }
    FileTab #ft-markdown.visible { display: block; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("e", "edit", "Edit mode", priority=True),
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
        self._cancel_pending: bool = False

    # --- compose ----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="ft-status", markup=True)
        yield Static(
            "⚠ file changed on disk — [r] reload (discard edits)  "
            "[k] keep mine",
            id="ft-warn", markup=False)
        yield Static(
            "⚠ unsaved edits — [d] discard  [esc] keep editing",
            id="ft-cancel", markup=False)
        lang = _EXT_LANGUAGE.get(self._path.suffix.lower())
        yield TextArea(language=lang, read_only=True, id="ft-editor")
        yield FocusableMarkdown(id="ft-markdown")

    async def on_mount(self) -> None:
        try:
            content = self._path.read_text(errors="replace")
            self._mtime = self._path.stat().st_mtime
        except OSError:
            content = f"(could not read {self._path})"
        self.query_one("#ft-editor", TextArea).load_text(content)
        if self._is_markdown:
            self.query_one("#ft-markdown", FocusableMarkdown).update(content)
        self._update_visibility()
        self._refresh_status()
        self.set_interval(_MTIME_POLL_S, self._check_mtime)

    @property
    def _is_markdown(self) -> bool:
        return self._path.suffix.lower() == ".md"

    def _update_visibility(self) -> None:
        show_md = self._is_markdown and not self._edit_mode
        self.query_one("#ft-markdown", FocusableMarkdown).set_class(show_md, "visible")
        self.query_one("#ft-editor", TextArea).set_class(show_md, "hidden")

    # --- status bar -------------------------------------------------

    def _refresh_status(self) -> None:
        mode = "EDIT" if self._edit_mode else "VIEW"
        # Make the mode indicator look like a clickable button
        mode_btn = fr"[@click=edit]\[{mode}][/]"
        mod = " *" if self._modified else ""
        try:
            loc = self.query_one("#ft-editor", TextArea).cursor_location
            pos = f"  {loc[0] + 1}:{loc[1] + 1}"
        except Exception:
            pos = ""
        text = f"{self._path}    {mode_btn}{mod}{pos}"
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
            if self._is_markdown:
                self.query_one("#ft-markdown", FocusableMarkdown).update(content)
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

    def action_edit(self) -> None:
        if not self._edit_mode:
            self._edit_mode = True
            self.query_one("#ft-editor", TextArea).read_only = False
            self._update_visibility()
            self._refresh_status()

    def key_r(self) -> None:
        if self._disk_changed:
            try:
                content = self._path.read_text(errors="replace")
                self._mtime = self._path.stat().st_mtime
                self.query_one("#ft-editor", TextArea).load_text(content)
                if self._is_markdown:
                    self.query_one("#ft-markdown", FocusableMarkdown).update(content)
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
        self._update_visibility()
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
            if self._is_markdown:
                self.query_one("#ft-markdown", FocusableMarkdown).update(content)
        except OSError:
            pass
        self._modified = False
        self._update_visibility()
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
            if self._is_markdown:
                self.query_one("#ft-markdown", FocusableMarkdown).update(content)
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
            if self._is_markdown and not self._edit_mode:
                self.query_one("#ft-markdown", FocusableMarkdown).focus()
            else:
                self.query_one("#ft-editor", TextArea).focus()

    async def close(self) -> None:
        pass
