# File Browser Tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Ctrl+O modal file picker with a persistent FileBrowserTab — a TUI tab with a recency-sorted file list, fuzzy filter, embedded FileTab editor, and a toggleable DirectoryTree sidebar.

**Architecture:** Add mtime tracking to FileIndexer, create FileBrowserTab as a new composite tab type that switches between browse and view modes, wire Ctrl+O to open a fresh FileBrowserTab instead of a modal.

**Tech Stack:** Textual 8.x (Widget, ContentSwitcher, DirectoryTree, OptionList, Input), Python 3.13+, existing FileIndexer + FileTab

**Spec:** `docs/superpowers/specs/2026-08-20-aegis-file-browser-tab-design.md`

## Global Constraints

- Python 3.13+; `uv run pytest -q -m "not live"` must stay green after every task
- No changes to `picker.py`, `file_tab.py`, `pane.py`, or `sidebar.py`
- `AegisApp.set_sidebar_mode` fans out via `getattr(pane, "set_task_dock", None)` — FileBrowserTab must expose `set_task_dock`, not `set_sidebar_mode`
- TDD: write failing test first, run it to confirm it fails, implement, confirm it passes, commit
- Commit per task using `git add <explicit paths>` (never `git add -A`)
- Run tests with `uv run pytest -q -m "not live"`

---

### Task 1: FileIndexer mtime layer

**Files:**
- Modify: `src/aegis/tui/file_index.py`
- Test: `tests/test_file_index.py`

**Interfaces:**
- Produces: `FileIndexer.paths_by_mtime() -> list[str]` — files sorted by mtime descending (most recently modified first); thread-safe snapshot

- [x] **Step 1: Write the failing tests**

Append to `tests/test_file_index.py`:

```python
import time


def test_paths_by_mtime_most_recent_first(tmp_path: Path):
    old = tmp_path / "old.py"
    old.write_text("x")
    time.sleep(0.02)
    new = tmp_path / "new.py"
    new.write_text("y")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    result = idx.paths_by_mtime()
    assert result.index("new.py") < result.index("old.py")
    idx.stop()


def test_paths_by_mtime_remove_clears_mtime(tmp_path: Path):
    f = tmp_path / "gone.py"
    f.write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "gone.py" in idx.paths_by_mtime()
    idx._remove(str(f))
    assert "gone.py" not in idx.paths_by_mtime()
    idx.stop()


def test_paths_by_mtime_add_inserts_with_mtime(tmp_path: Path):
    f = tmp_path / "added.py"
    f.write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    new_f = tmp_path / "newest.py"
    new_f.write_text("y")
    idx._add(str(new_f))
    result = idx.paths_by_mtime()
    assert "newest.py" in result
    assert result.index("newest.py") == 0   # newest is first
    idx.stop()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
cd repos/aegis && uv run pytest tests/test_file_index.py::test_paths_by_mtime_most_recent_first tests/test_file_index.py::test_paths_by_mtime_remove_clears_mtime tests/test_file_index.py::test_paths_by_mtime_add_inserts_with_mtime -v
```

Expected: FAIL with `AttributeError: 'FileIndexer' object has no attribute 'paths_by_mtime'`

- [x] **Step 3: Implement mtime layer in FileIndexer**

In `src/aegis/tui/file_index.py`:

Add `_mtimes: dict[str, float]` to `__init__`:

```python
def __init__(self) -> None:
    self._paths: list[str] = []
    self._mtimes: dict[str, float] = {}   # rel_path -> mtime
    self._cwd: Path | None = None
    self._observer: Observer | None = None
    self._ready = threading.Event()
    self._lock = threading.Lock()
```

Add `paths_by_mtime` public method after the `filter` method:

```python
def paths_by_mtime(self) -> list[str]:
    """Files sorted by mtime descending (most recently touched first)."""
    with self._lock:
        # Files absent from _mtimes sort last (0.0 fallback).
        return sorted(
            self._paths,
            key=lambda p: self._mtimes.get(p, 0.0),
            reverse=True,
        )
```

In `_walk`, capture mtime when building the path list. Change the inner loop to:

```python
for fname in files:
    if _ignore_file(Path(fname)):
        continue
    full = os.path.join(root, fname)
    rel = full[prefix:] if full.startswith(str(cwd)) else full
    paths.append(rel)
    try:
        mtimes[rel] = os.stat(full).st_mtime
    except OSError:
        pass
    if len(paths) % self.PUBLISH_EVERY == 0:
        self._publish(paths, mtimes)
        time.sleep(0)
```

Add `mtimes: dict[str, float] = {}` at the top of `_walk` (before the `os.walk` loop), then change `_publish` to also store mtimes:

```python
def _publish(self, paths: list[str], mtimes: dict[str, float] | None = None) -> None:
    with self._lock:
        self._paths = sorted(paths)
        if mtimes is not None:
            self._mtimes.update(mtimes)
```

In `_add`, capture mtime:

```python
def _add(self, abs_path: str) -> None:
    cwd = self._cwd
    if cwd is None:
        return
    fp = Path(abs_path)
    if not fp.is_file() or _ignore_file(fp):
        return
    try:
        rel = str(fp.relative_to(cwd))
    except ValueError:
        return
    parts = Path(rel).parts
    if any(_ignore_dir(p) for p in parts[:-1]):
        return
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        mtime = 0.0
    with self._lock:
        i = bisect.bisect_left(self._paths, rel)
        if i == len(self._paths) or self._paths[i] != rel:
            self._paths.insert(i, rel)
        self._mtimes[rel] = mtime
```

In `_remove`, also delete from `_mtimes`:

```python
def _remove(self, abs_path: str) -> None:
    cwd = self._cwd
    if cwd is None:
        return
    try:
        rel = str(Path(abs_path).relative_to(cwd))
    except ValueError:
        return
    with self._lock:
        try:
            self._paths.remove(rel)
        except ValueError:
            pass
        self._mtimes.pop(rel, None)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_file_index.py -v
```

Expected: all pass (including pre-existing tests)

- [x] **Step 5: Commit**

```bash
git add src/aegis/tui/file_index.py tests/test_file_index.py
git commit -m "feat(file-browser): mtime layer on FileIndexer — paths_by_mtime()"
```

---

### Task 2: FileBrowserTab skeleton — tab contract + layout

**Files:**
- Create: `src/aegis/tui/file_browser_tab.py`
- Create: `tests/test_file_browser_tab.py`

**Interfaces:**
- Consumes: `FileIndexer` (from Task 1), `FileTab` (existing), `AgentState` (existing `tui/state.py`)
- Produces:
  - `FileBrowserTab(cwd: Path, indexer: FileIndexer, *, prefill: str = "", sidebar_open: bool = False)` — Widget subclass
  - `tab.handle: str` — `"browser:<n>"`
  - `tab.agent_slug: str` — `"browser"`
  - `tab.state: AgentState` — always `AgentState.ready`
  - `tab.unseen: bool` — always `False`
  - `tab.set_task_dock(opened: bool) -> bool` — shows/hides `#fb-sidebar`
  - `tab.focus_input() -> None`
  - `tab.close() -> None` (no-op)

- [x] **Step 1: Write the failing tests**

Create `tests/test_file_browser_tab.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher

from aegis.tui.file_browser_tab import FileBrowserTab
from aegis.tui.file_index import FileIndexer
from aegis.tui.state import AgentState


def _make_tab(tmp_path: Path, *, prefill: str = "", sidebar_open: bool = False) -> FileBrowserTab:
    idx = FileIndexer()
    return FileBrowserTab(cwd=tmp_path, indexer=idx, prefill=prefill, sidebar_open=sidebar_open)


class _Host(App):
    def __init__(self, tab: FileBrowserTab) -> None:
        super().__init__()
        self._tab = tab

    def compose(self) -> ComposeResult:
        yield ContentSwitcher(id="cs")

    async def on_mount(self) -> None:
        cs = self.query_one("#cs", ContentSwitcher)
        cs.display = False
        await cs.mount(self._tab)
        cs.current = self._tab.id


def test_quacks_like_pane(tmp_path: Path):
    tab = _make_tab(tmp_path)
    assert isinstance(tab.handle, str)
    assert tab.handle.startswith("browser:")
    assert tab.agent_slug == "browser"
    assert tab.state is AgentState.ready
    assert tab.unseen is False
    assert tab.id is not None


def test_multiple_tabs_have_distinct_handles(tmp_path: Path):
    idx = FileIndexer()
    t1 = FileBrowserTab(cwd=tmp_path, indexer=idx)
    t2 = FileBrowserTab(cwd=tmp_path, indexer=idx)
    assert t1.handle != t2.handle
    assert t1.id != t2.id


@pytest.mark.asyncio
async def test_set_task_dock_hides_sidebar(tmp_path: Path):
    tab = _make_tab(tmp_path, sidebar_open=True)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = tab.query_one("#fb-sidebar")
        assert sidebar.display is True
        tab.set_task_dock(False)
        await pilot.pause()
        assert sidebar.display is False


@pytest.mark.asyncio
async def test_set_task_dock_shows_sidebar(tmp_path: Path):
    tab = _make_tab(tmp_path, sidebar_open=False)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = tab.query_one("#fb-sidebar")
        assert sidebar.display is False
        tab.set_task_dock(True)
        await pilot.pause()
        assert sidebar.display is True
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_file_browser_tab.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.tui.file_browser_tab'`

- [x] **Step 3: Implement the skeleton**

Create `src/aegis/tui/file_browser_tab.py`:

```python
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
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_file_browser_tab.py -v
```

Expected: all 4 tests pass

- [x] **Step 5: Run full suite**

```bash
uv run pytest -q -m "not live"
```

Expected: all green

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/file_browser_tab.py tests/test_file_browser_tab.py
git commit -m "feat(file-browser): FileBrowserTab skeleton — tab contract + browse layout"
```

---

### Task 3: Browse mode — filter + list interaction

**Files:**
- Modify: `src/aegis/tui/file_browser_tab.py`
- Modify: `tests/test_file_browser_tab.py`

**Interfaces:**
- Consumes: `FileBrowserTab` from Task 2
- Produces:
  - `Input.Changed` → re-renders list filtered by mtime order
  - `OptionList.OptionSelected` → stores selected path as `_current_file`, switches to view mode
  - Internal `_switch_to_view(path: Path)` — mounts FileTab in `#fb-view`, shows view, hides browse
  - Internal `_switch_to_browse()` — shows browse, hides view, restores filter focus

- [x] **Step 1: Write the failing tests**

Append to `tests/test_file_browser_tab.py`:

```python
import time as _time


@pytest.mark.asyncio
async def test_filter_narrows_list(tmp_path: Path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "beta.py").write_text("y")
    idx = FileIndexer()
    idx.start(tmp_path)

    def _wait_ready():
        import threading
        assert idx._ready.wait(5.0)
    _wait_ready()

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        filt = tab.query_one("#fb-filter", Input)
        await pilot.click("#fb-filter")
        await pilot.type("alpha")
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        labels = [ol.get_option_at_index(i).prompt for i in range(ol.option_count)]
        assert any("alpha.py" in lbl for lbl in labels)
        assert not any("beta.py" in lbl for lbl in labels)
    idx.stop()


@pytest.mark.asyncio
async def test_selecting_file_switches_to_view(tmp_path: Path):
    f = tmp_path / "target.py"
    f.write_text("print('hi')")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        # Select the first option (target.py is the only file)
        ol.action_select()
        await pilot.pause()
        view = tab.query_one("#fb-view")
        browse = tab.query_one("#fb-browse")
        assert view.display is True
        assert browse.display is False
        assert tab._current_file is not None
    idx.stop()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_file_browser_tab.py::test_filter_narrows_list tests/test_file_browser_tab.py::test_selecting_file_switches_to_view -v
```

Expected: `test_filter_narrows_list` FAIL (filter not wired), `test_selecting_file_switches_to_view` FAIL (no switch logic)

- [x] **Step 3: Implement filter wiring and view switch**

In `src/aegis/tui/file_browser_tab.py`, add these methods to `FileBrowserTab`:

```python
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

async def _switch_to_view(self, path: Path) -> None:
    import contextlib
    from aegis.tui.file_tab import FileTab
    self._current_file = path
    # Remove any previously mounted FileTab
    with contextlib.suppress(Exception):
        old = self.query_one("#fb-embedded-file")
        await old.remove()
    ft = FileTab(path, id_override=None)
    # Give the embedded FileTab a stable inner id
    ft._FileBrowserTab__embedded = True   # marker only
    view_container = self.query_one("#fb-view")
    # Clear the placeholder
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
```

**Note on FileTab mounting:** `FileTab.__init__` currently derives its `id` from the file path hash. When embedded inside `FileBrowserTab` we mount it directly without needing a stable dedup id — the `FileBrowserTab` manages its own lifecycle. The existing `FileTab` constructor signature is `FileTab(path, *, line=None)`. Pass just the path:

```python
ft = FileTab(path)
```

The embedded FileTab will have its own `id`; remove it by targeting `FileTab` via `self.query_one(FileTab)` rather than by id. Update `_switch_to_view` accordingly:

```python
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
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_file_browser_tab.py -v
```

Expected: all pass

- [x] **Step 5: Run full suite**

```bash
uv run pytest -q -m "not live"
```

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/file_browser_tab.py tests/test_file_browser_tab.py
git commit -m "feat(file-browser): browse mode — filter, age labels, file selection → view"
```

---

### Task 4: View mode — back-to-browse + prefill shortcut

**Files:**
- Modify: `src/aegis/tui/file_browser_tab.py`
- Modify: `tests/test_file_browser_tab.py`

**Interfaces:**
- Consumes: `FileBrowserTab` from Task 3
- Produces:
  - `b` key in view mode → `_show_browse()`, restores filter focus
  - `Esc` key in view mode when FileTab is in VIEW/PREVIEW (not EDIT) → `_show_browse()`
  - `prefill` pointing at an existing file → opens directly in view mode on mount
  - `focus_input()` — focuses TextArea when in view, Input when in browse

- [x] **Step 1: Write the failing tests**

Append to `tests/test_file_browser_tab.py`:

```python
@pytest.mark.asyncio
async def test_b_key_returns_to_browse(tmp_path: Path):
    f = tmp_path / "back.py"
    f.write_text("x = 1")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Switch to view first
        await tab._switch_to_view(f)
        await pilot.pause()
        assert tab.query_one("#fb-view").display is True
        # Press b
        await pilot.press("b")
        await pilot.pause()
        assert tab.query_one("#fb-browse").display is True
        assert tab.query_one("#fb-view").display is False
    idx.stop()


@pytest.mark.asyncio
async def test_prefill_existing_file_opens_view(tmp_path: Path):
    f = tmp_path / "preopen.py"
    f.write_text("y = 2")
    idx = FileIndexer()
    # Don't start indexer — prefill by path, not from index
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx, prefill=str(f))
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()   # second pause for mount + open
        assert tab._current_file == f.resolve()
        assert tab.query_one("#fb-view").display is True
    idx.stop()


@pytest.mark.asyncio
async def test_prefill_nonexistent_populates_filter(tmp_path: Path):
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx, prefill="myfile")
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        filt = tab.query_one("#fb-filter", Input)
        assert filt.value == "myfile"
        assert tab._current_file is None
    idx.stop()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_file_browser_tab.py::test_b_key_returns_to_browse tests/test_file_browser_tab.py::test_prefill_existing_file_opens_view tests/test_file_browser_tab.py::test_prefill_nonexistent_populates_filter -v
```

Expected: FAIL (no `b` key handler, no prefill-to-view logic)

- [x] **Step 3: Implement back-to-browse and prefill**

Add to `FileBrowserTab` in `file_browser_tab.py`:

```python
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
```

(Replace the existing `on_mount` which only has the first three lines.)

Add `key_b` handler:

```python
def key_b(self) -> None:
    """Return to browse mode from view mode."""
    if self._current_file is not None:
        self._current_file = None
        self._show_browse()
```

Update `focus_input` to correctly target the TextArea inside the embedded FileTab:

```python
def focus_input(self) -> None:
    import contextlib
    from textual.widgets import TextArea
    if self._current_file is not None:
        with contextlib.suppress(Exception):
            self.query_one(TextArea).focus()
            return
    with contextlib.suppress(Exception):
        self.query_one("#fb-filter", Input).focus()
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_file_browser_tab.py -v
```

Expected: all pass

- [x] **Step 5: Run full suite**

```bash
uv run pytest -q -m "not live"
```

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/file_browser_tab.py tests/test_file_browser_tab.py
git commit -m "feat(file-browser): view mode — back-to-browse (b key) + prefill shortcut"
```

---

### Task 5: Tree sidebar — DirectoryTree file selection

**Files:**
- Modify: `src/aegis/tui/file_browser_tab.py`
- Modify: `tests/test_file_browser_tab.py`

**Interfaces:**
- Consumes: `FileBrowserTab` from Task 4, `DirectoryTree.FileSelected` message
- Produces:
  - `on_directory_tree_file_selected` → calls `_switch_to_view(path)`

- [x] **Step 1: Write the failing test**

Append to `tests/test_file_browser_tab.py`:

```python
from textual.widgets import DirectoryTree


@pytest.mark.asyncio
async def test_tree_file_selected_opens_view(tmp_path: Path):
    f = tmp_path / "treefile.py"
    f.write_text("z = 3")
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx, sidebar_open=True)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Simulate a FileSelected message from the DirectoryTree
        tab.post_message(DirectoryTree.FileSelected(
            tab.query_one("#fb-tree", DirectoryTree),
            f,
        ))
        await pilot.pause()
        assert tab._current_file == f.resolve()
        assert tab.query_one("#fb-view").display is True
    idx.stop()
```

- [x] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_file_browser_tab.py::test_tree_file_selected_opens_view -v
```

Expected: FAIL (no `on_directory_tree_file_selected` handler)

- [x] **Step 3: Implement the handler**

Add to `FileBrowserTab`:

```python
async def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected) -> None:
    event.stop()
    if event.path.is_file():
        await self._switch_to_view(event.path.resolve())
```

Add the import at the top of `file_browser_tab.py` (it's already imported via `textual.widgets`; `DirectoryTree` is already in the compose — just confirm the import line includes it):

The existing import should already be:
```python
from textual.widgets import DirectoryTree, Input, Label, OptionList, Static
```

- [x] **Step 4: Run all tests to verify they pass**

```bash
uv run pytest tests/test_file_browser_tab.py -v
```

Expected: all pass

- [x] **Step 5: Run full suite**

```bash
uv run pytest -q -m "not live"
```

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/file_browser_tab.py tests/test_file_browser_tab.py
git commit -m "feat(file-browser): tree sidebar — DirectoryTree.FileSelected opens view mode"
```

---

### Task 6: Ctrl+O wiring in app.py

**Files:**
- Modify: `src/aegis/tui/app.py`
- Test: manual smoke only (the app.py action is end-to-end TUI; see notes)

**Interfaces:**
- Consumes: `FileBrowserTab` from Tasks 2–5
- Produces:
  - `AegisApp.action_open_file_picker(prefill="")` creates a `FileBrowserTab`, mounts it as a new pane, switches to it
  - Multiple invocations → multiple independent browser tabs

**Note on testing:** `action_open_file_picker` drives `AegisApp` directly, which requires spinning up the full app with a real (or mocked) `_file_indexer`. This is an integration test in the same class as `test_integration_live.py`. Rather than write a slow live test here, the deliverable is verified by running `aegis` interactively and pressing Ctrl+O. The existing `test_file_browser_tab.py` tests cover all the tab behaviour.

- [x] **Step 1: Locate the current implementation**

In `src/aegis/tui/app.py`, find `action_open_file_picker` at line ~1358:

```python
@work
async def action_open_file_picker(self, prefill: str = "") -> None:
    from aegis.tui.picker import FilePickerModal
    path = await self.push_screen_wait(FilePickerModal(prefill=prefill))
    if path is not None:
        await self._open_file_tab(path)
```

- [x] **Step 2: Replace with FileBrowserTab creation**

Replace the method body:

```python
async def action_open_file_picker(self, prefill: str = "") -> None:
    from aegis.tui.file_browser_tab import FileBrowserTab
    cwd = self._file_indexer._cwd or Path.cwd()
    tab = FileBrowserTab(
        cwd=cwd,
        indexer=self._file_indexer,
        prefill=prefill,
        sidebar_open=self.sidebar_mode,
    )
    self._panes.append(tab)
    cs = self.query_one(ContentSwitcher)
    tab.display = False   # hidden until ContentSwitcher activates it
    await cs.mount(tab)
    cs.current = tab.id
    self._refresh_tabbar()
    tab.focus_input()
```

Remove the `@work` decorator — the new implementation is a plain `async def` that does its own `await`. (The old version used `@work` because `push_screen_wait` needs a worker context; the new version does not block on a screen.)

Also verify that `Path` is already imported at the top of `app.py` (it is, as `from pathlib import Path`).

- [x] **Step 3: Run full suite**

```bash
uv run pytest -q -m "not live"
```

Expected: all green (the wiring change has no unit test coverage, but nothing that had tests should break)

- [x] **Step 4: Smoke test interactively**

```bash
cd /path/to/your/project && aegis
```

- Press `Ctrl+O` — a new tab labelled `browser:1` should appear
- The tab shows a filter input and a file list sorted by most-recently-modified
- Typing in the filter should narrow the list
- Pressing Enter on a file should show the file's content in the editor
- Pressing `b` should return to the file list
- Press `F3` — the tree panel should appear on the right
- Press `Ctrl+O` again — a second browser tab (`browser:2`) should open

- [x] **Step 5: Commit**

```bash
git add src/aegis/tui/app.py
git commit -m "feat(file-browser): wire Ctrl+O to FileBrowserTab — replaces modal picker"
```

- [x] **Step 6: Push**

```bash
git push origin main
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Mtime layer on FileIndexer, `paths_by_mtime()` | Task 1 |
| FileBrowserTab tab contract (handle, agent_slug, state, unseen, focus_input, close) | Task 2 |
| set_task_dock — F3 fan-out participation | Task 2 |
| Browse mode: recency-sorted list, age labels, filter | Tasks 2+3 |
| Filter narrows in mtime order (not alphabetically re-sorted) | Task 3 |
| View mode: embedded FileTab | Task 3 |
| `b` / back-to-browse from view mode | Task 4 |
| prefill → view mode if file exists | Task 4 |
| prefill → filter populated if not a file | Task 4 |
| DirectoryTree sidebar, FileSelected → view mode | Task 5 |
| Ctrl+O opens fresh FileBrowserTab (multi-instance) | Task 6 |
| Sidebar adopts current app sidebar_mode on mount | Task 2 (constructor `sidebar_open` param) |
| FilePickerModal unchanged | Not touched |

**No placeholders found.**

**Type consistency:**
- `paths_by_mtime() -> list[str]` defined Task 1, consumed Task 2 ✓
- `FileBrowserTab(cwd, indexer, *, prefill, sidebar_open)` defined Task 2, consumed Task 6 ✓
- `set_task_dock(opened: bool) -> bool` defined Task 2, relied on by app.py fan-out ✓
- `_switch_to_view(path: Path)` defined Task 3, called by Task 4 (prefill) and Task 5 (tree) ✓
- `_show_browse()` / `_show_view()` defined Task 3, called Task 4 ✓
