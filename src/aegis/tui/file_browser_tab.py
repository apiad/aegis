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
from textual.widgets import DirectoryTree, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from aegis.tui.file_index import FileIndexer
from aegis.tui.state import AgentState

_COUNTER = itertools.count(1)
_SIDEBAR_WIDTH = 28
_POLL_S = 2.0


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

    # --- tab contract -----------------------------------------------

    def focus_input(self) -> None:
        from textual.widgets import TextArea
        import contextlib
        if self._current_file is not None:
            with contextlib.suppress(Exception):
                self.query_one(TextArea).focus()
        else:
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

    # --- list management --------------------------------------------

    def _refresh_list(self) -> None:
        import contextlib
        import time
        paths = self._indexer.paths_by_mtime()
        if self._filter_text:
            needle = self._filter_text.lower()
            paths = [p for p in paths if needle in p.lower()]
        now = time.time()
        mtimes = self._indexer._mtimes
        options = []
        for p in paths[:200]:   # cap to keep the widget fast
            mtime = mtimes.get(p, 0.0)
            age = _fmt_age(now - mtime) if mtime else "?"
            options.append(Option(f"{age:>5}  {p}", id=p))
        with contextlib.suppress(Exception):
            ol = self.query_one("#fb-list", OptionList)
            ol.clear_options()
            for opt in options:
                ol.add_option(opt)
