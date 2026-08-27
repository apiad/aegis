"""FileBrowserTab — persistent file browser and editor tab.

Browse mode (default): recency-sorted file list + fuzzy filter.
View mode: embedded FileTab editor.
Right sidebar: DirectoryTree, toggled by F3 (set_task_dock).
"""
from __future__ import annotations

import itertools
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DirectoryTree, Input, OptionList, Static
from textual.widgets.option_list import Option

from aegis.tui.file_index import FileIndexer
from aegis.tui.state import AgentState

_COUNTER = itertools.count(1)
_SIDEBAR_WIDTH = 28
_POLL_S = 2.0
_LIST_CAP = 200


def _fmt_age(seconds: float) -> str:
    """Human-readable relative age from an elapsed-seconds float."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 24:
        return f"{h}h"
    d = h // 24
    if d > 30:
        return ">30d"
    return f"{d}d"


class FileBrowserTab(Widget, can_focus=True):
    """Composite browser/editor tab opened by Ctrl+O."""

    DEFAULT_CSS = f"""
    FileBrowserTab {{
        layout: horizontal;
        height: 1fr;
    }}
    FileBrowserTab #fb-main {{
        width: 1fr;
        layout: vertical;
    }}
    FileBrowserTab #fb-sidebar {{
        width: {_SIDEBAR_WIDTH};
        layout: vertical;
        border-left: solid $panel;
    }}
    FileBrowserTab #fb-browse {{
        layout: vertical;
        height: 1fr;
    }}
    FileBrowserTab #fb-view {{
        layout: vertical;
        height: 1fr;
        display: none;
    }}
    FileBrowserTab #fb-view.active {{
        display: block;
    }}
    FileBrowserTab #fb-browse.hidden {{
        display: none;
    }}
    FileBrowserTab #fb-filter {{
        height: 1;
        dock: top;
        border: none;
        padding: 0 1;
    }}
    FileBrowserTab #fb-list {{
        height: 1fr;
    }}
    """

    def __init__(
        self,
        cwd: Path,
        indexer: FileIndexer,
        *,
        prefill: str = "",
        sidebar_open: bool = False,
    ) -> None:
        n = next(_COUNTER)
        super().__init__(id=f"browsertab-{n}")
        self.handle: str = f"browser:{n}"
        self.agent_slug: str = "browser"
        self.state: AgentState = AgentState.ready
        self.unseen: bool = False
        self._cwd = cwd.resolve()
        self._indexer = indexer
        self._prefill = prefill
        self._sidebar_open = sidebar_open
        self._filter_text: str = prefill
        self._current_file: Path | None = None
        self._pending_highlight: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="fb-main"):
                with Vertical(id="fb-browse"):
                    yield Input(value=self._prefill,
                                placeholder="filter files…",
                                id="fb-filter")
                    yield OptionList(id="fb-list")
                with Vertical(id="fb-view"):
                    yield Static("", id="fb-view-placeholder")
            with Vertical(id="fb-sidebar"):
                yield DirectoryTree(str(self._cwd), id="fb-tree")

    async def on_mount(self) -> None:
        self.set_task_dock(self._sidebar_open)
        self._refresh_list()
        self.set_interval(_POLL_S, self._refresh_list)
        # If prefill is an existing file path, open it directly in view mode.
        if self._prefill:
            candidate = Path(self._prefill)
            if not candidate.is_absolute():
                candidate = self._cwd / self._prefill
            if candidate.is_file():
                await self._switch_to_view(candidate.resolve())

    # --- tab contract -----------------------------------------------

    def focus_input(self) -> None:
        import contextlib
        from textual.widgets import TextArea
        if self._current_file is not None:
            with contextlib.suppress(Exception):
                self.query_one(TextArea).focus()
                return
        with contextlib.suppress(Exception):
            self.query_one("#fb-filter", Input).focus()

    def set_task_dock(self, opened: bool) -> bool:
        import contextlib
        with contextlib.suppress(Exception):
            sidebar = self.query_one("#fb-sidebar")
            sidebar.display = opened
            return True
        return False

    async def close(self) -> None:
        pass

    # --- event handlers --------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "fb-filter":
            self._filter_text = event.value
            self._refresh_list()

    async def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        rel = event.option.id
        if rel is None:
            return
        path = self._cwd / rel
        if path.is_file():
            await self._switch_to_view(path)

    async def on_directory_tree_file_selected(
            self, event: DirectoryTree.FileSelected) -> None:
        event.stop()
        if event.path.is_file():
            await self._switch_to_view(event.path.resolve())

    # --- list management --------------------------------------------

    def _refresh_list(self) -> None:
        import contextlib
        import time
        # The poll keeps firing in view mode, where the list is hidden.
        # Rebuilding it there is wasted work and throws away the highlight
        # that returning to browse is about to restore.
        if self._current_file is not None:
            return
        # Note: this filter is a substring match over the mtime-sorted
        # snapshot, deliberately not `indexer.filter()` — that one re-sorts
        # alphabetically and caps at 50, which would undo the recency order
        # the whole tab exists for.
        paths = self._indexer.paths_by_mtime()
        if self._filter_text:
            needle = self._filter_text.lower()
            paths = [p for p in paths if needle in p.lower()]
        now = time.time()
        mtimes = self._indexer._mtimes
        options = []
        for p in paths[:_LIST_CAP]:
            mtime = mtimes.get(p, 0.0)
            age = _fmt_age(now - mtime) if mtime else "?"
            options.append(Option(f"{age:>5}  {p}", id=p))
        dropped = len(paths) - len(options)
        if dropped > 0:
            # A silent cap reads as a complete listing.
            options.append(Option(
                f"       … {dropped} more — narrow the filter", disabled=True))
        with contextlib.suppress(Exception):
            ol = self.query_one("#fb-list", OptionList)
            want = self._pending_highlight or self._highlighted_id(ol)
            self._pending_highlight = None
            ol.clear_options()
            for opt in options:
                ol.add_option(opt)
            if want is not None:
                for i, opt in enumerate(options):
                    if opt.id == want:
                        ol.highlighted = i
                        break

    @staticmethod
    def _highlighted_id(ol: OptionList) -> str | None:
        i = ol.highlighted
        if i is None or i >= ol.option_count:
            return None
        return ol.get_option_at_index(i).id

    async def _switch_to_view(self, path: Path) -> None:
        import contextlib
        from aegis.tui.file_tab import FileTab
        self._current_file = path
        # Remove previously mounted FileTab if any
        with contextlib.suppress(Exception):
            old = self.query_one(FileTab)
            await old.remove()
        ft = FileTab(path)
        view_container = self.query_one("#fb-view")
        with contextlib.suppress(Exception):
            self.query_one("#fb-view-placeholder", Static).display = False
        await view_container.mount(ft)
        self._show_view()

    def _show_view(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self.query_one("#fb-view").add_class("active")
            self.query_one("#fb-browse").add_class("hidden")

    def _show_browse(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self.query_one("#fb-view").remove_class("active")
            self.query_one("#fb-browse").remove_class("hidden")
            self.query_one("#fb-filter", Input).focus()

    def key_b(self) -> None:
        """Return to browse mode from view mode."""
        self._back_to_browse()

    def escape_handled(self) -> bool:
        """The escape rung ``AegisApp.action_interrupt`` calls.

        The app binds escape at priority, so a focused widget's
        ``key_escape`` never runs inside the real app — the binding wins
        and the widget is skipped. Escape reaches a tab through this
        duck-typed hook instead. Answering ``False`` leaves the key to the
        rungs below (cancel a note, clear the input, interrupt the turn).
        """
        import contextlib
        from aegis.tui.file_tab import FileTab
        if self._current_file is None:
            return False
        # An editing or previewing editor owns escape first — leaving edit
        # mode must not also abandon the file you were editing.
        with contextlib.suppress(Exception):
            if self.query_one(FileTab).escape_handled():
                return True
        self._back_to_browse()
        return True

    def _back_to_browse(self) -> None:
        import contextlib
        path = self._current_file
        if path is None:
            return
        self._current_file = None
        with contextlib.suppress(ValueError):
            self._pending_highlight = str(path.resolve().relative_to(self._cwd))
        self._show_browse()
        self._refresh_list()
