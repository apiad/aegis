# Aegis File Viewer/Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `FileTab` widget that lets users view and edit any file from the Aegis TUI, opened via Ctrl+O fuzzy picker, clicking backtick tokens in agent responses, or the `aegis_view_file` MCP tool.

**Architecture:** `FileTab` is a new tab type (alongside `ConversationPane` and `TerminalTab`) mounted in the `ContentSwitcher`. A `FilePickerModal` (added to `tui/picker.py`) provides fuzzy file selection. File-change detection uses `set_interval` mtime polling — no external dependencies. The MCP tool calls `bridge.open_file(path)` if available (TUI-only, not headless).

**Tech Stack:** Python 3.13, Textual 8.x (`TextEditor`, `ModalScreen`, `set_interval`), `textual[syntax]` extra for tree-sitter highlighting, FastMCP for the tool registration.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | Modify | Add `textual[syntax]` extra |
| `src/aegis/tui/file_tab.py` | **Create** | `FileTab` widget |
| `src/aegis/tui/picker.py` | Modify | Add `FilePickerModal` |
| `src/aegis/tui/pane.py` | Modify | Backtick-click opens picker |
| `src/aegis/tui/app.py` | Modify | Ctrl+O binding, `_open_file_tab`, `open_file` |
| `src/aegis/mcp/server.py` | Modify | `aegis_view_file` tool |
| `tests/test_file_tab.py` | **Create** | FileTab unit tests |
| `tests/test_file_picker.py` | **Create** | FilePickerModal unit tests |
| `tests/test_mcp_view_file.py` | **Create** | MCP tool tests |

---

## Task 1: Add textual[syntax] dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update the dependency**

In `pyproject.toml`, find the line:
```
"textual>=8.2.6",
```
Change it to:
```
"textual[syntax]>=8.2.6",
```

- [ ] **Step 2: Re-lock and install**

```bash
cd repos/aegis
uv sync
```
Expected: lock file updates, tree-sitter packages install.

- [ ] **Step 3: Verify import**

```bash
python -c "from textual.widgets import TextEditor; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add textual[syntax] for tree-sitter highlighting"
```

---

## Task 2: FilePickerModal

**Files:**
- Modify: `src/aegis/tui/picker.py`
- Create: `tests/test_file_picker.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_file_picker.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from aegis.tui.picker import FilePickerModal


class _Host(App):
    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill
        self.result: Path | None = "not_set"  # type: ignore[assignment]

    def compose(self) -> ComposeResult:
        yield FilePickerModal(prefill=self._prefill)

    def on_screen_resume(self) -> None:
        pass


@pytest.mark.asyncio
async def test_file_picker_mounts(tmp_path: Path):
    (tmp_path / "hello.py").write_text("print('hi')")
    import os
    os.chdir(tmp_path)
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Modal is on screen — Input should be focused
        from textual.widgets import Input
        inp = app.query_one(Input)
        assert inp.value == ""


@pytest.mark.asyncio
async def test_file_picker_prefill(tmp_path: Path):
    (tmp_path / "myfile.py").write_text("x = 1")
    import os
    os.chdir(tmp_path)
    app = _Host(prefill="myfile")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input
        inp = app.query_one(Input)
        assert inp.value == "myfile"


@pytest.mark.asyncio
async def test_file_picker_escape_returns_none(tmp_path: Path):
    import os
    os.chdir(tmp_path)
    result_holder = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            path = await self.push_screen_wait(FilePickerModal())
            result_holder.append(path)
            self.exit()

    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert result_holder == [None]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_file_picker.py -v
```
Expected: `ImportError` or `AttributeError` — `FilePickerModal` does not exist yet.

- [ ] **Step 3: Add FilePickerModal to picker.py**

Open `src/aegis/tui/picker.py`. After the existing imports, add `Path` and `re` to imports. Then append this class at the end of the file:

```python
from pathlib import Path


class FilePickerModal(ModalScreen):
    """Fuzzy file picker modal. Dismisses with a resolved Path or None."""

    DEFAULT_CSS = """
    FilePickerModal { align: center middle; }
    FilePickerModal #fp-box {
        width: 70; max-height: 22;
        border: round $panel; background: $surface; padding: 1 2;
    }
    FilePickerModal Input { width: 100%; margin-bottom: 1; border: none;
                            background: $background; }
    FilePickerModal OptionList { width: 100%; max-height: 16;
                                 border: none; background: $surface; }
    """

    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill
        self._all_paths: list[str] = []

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        with Vertical(id="fp-box"):
            yield Input(placeholder="type to filter files…", id="fp-input")
            yield OptionList(id="fp-list")

    def on_mount(self) -> None:
        cwd = Path.cwd()
        paths: list[str] = []
        try:
            for p in sorted(cwd.rglob("*")):
                if p.is_file():
                    try:
                        paths.append(str(p.relative_to(cwd)))
                    except ValueError:
                        paths.append(str(p))
                if len(paths) >= 5000:
                    break
        except PermissionError:
            pass
        self._all_paths = paths
        inp = self.query_one("#fp-input", Input)
        if self._prefill:
            inp.value = self._prefill
        inp.focus()
        self._filter(self._prefill)

    def _filter(self, text: str) -> None:
        ol = self.query_one("#fp-list", OptionList)
        ol.clear_options()
        needle = text.lower()
        matches = (
            [p for p in self._all_paths if needle in p.lower()]
            if needle
            else self._all_paths[:50]
        )
        for p in matches[:50]:
            ol.add_option(Option(p, id=p))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._select_highlighted()

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        if opt_id:
            self.dismiss(Path.cwd() / opt_id)
        else:
            self.dismiss(None)

    def _select_highlighted(self) -> None:
        ol = self.query_one("#fp-list", OptionList)
        try:
            highlighted = ol.highlighted
            if highlighted is not None:
                opt = ol.get_option_at_index(highlighted)
                if opt.id:
                    self.dismiss(Path.cwd() / opt.id)
                    return
        except Exception:
            pass
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)

    def key_enter(self) -> None:
        self._select_highlighted()
```

Also add `from textual.containers import Vertical` to the top of picker.py if not already present (check — the existing classes use `OptionList`, `Input`, `Option` so those are already imported).

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_file_picker.py -v
```
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/picker.py tests/test_file_picker.py
git commit -m "feat(tui): add FilePickerModal — fuzzy file picker"
```

---

## Task 3: FileTab — core widget

**Files:**
- Create: `src/aegis/tui/file_tab.py`
- Create: `tests/test_file_tab.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_file_tab.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher

from aegis.tui.file_tab import FileTab
from aegis.tui.state import AgentState


class _Host(App):
    def __init__(self, tab: FileTab) -> None:
        super().__init__()
        self._tab = tab

    def compose(self) -> ComposeResult:
        yield ContentSwitcher(id="cs")

    async def on_mount(self) -> None:
        cs = self.query_one("#cs", ContentSwitcher)
        await cs.mount(self._tab)
        cs.current = self._tab.id


@pytest.mark.asyncio
async def test_file_tab_loads_content(tmp_path: Path):
    f = tmp_path / "hello.py"
    f.write_text("print('hello')")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import TextEditor
        editor = tab.query_one(TextEditor)
        assert "print" in editor.text
        assert editor.read_only is True


def test_file_tab_quacks_like_pane(tmp_path: Path):
    """FileTab must expose handle, agent_slug, state, unseen, id."""
    f = tmp_path / "x.py"
    f.write_text("")
    tab = FileTab(f)
    assert isinstance(tab.handle, str)
    assert tab.agent_slug == "file"
    assert tab.state is AgentState.ready
    assert tab.unseen is False
    assert tab.id is not None


@pytest.mark.asyncio
async def test_file_tab_edit_mode_toggle(tmp_path: Path):
    f = tmp_path / "edit.py"
    f.write_text("x = 1")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import TextEditor
        editor = tab.query_one(TextEditor)
        assert editor.read_only is True
        await pilot.press("e")
        await pilot.pause()
        assert editor.read_only is False


@pytest.mark.asyncio
async def test_file_tab_save(tmp_path: Path):
    f = tmp_path / "save_me.py"
    f.write_text("old content")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        from textual.widgets import TextEditor
        editor = tab.query_one(TextEditor)
        editor.load_text("new content")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert f.read_text() == "new content"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_file_tab.py -v
```
Expected: `ImportError` — `file_tab` module does not exist.

- [ ] **Step 3: Create src/aegis/tui/file_tab.py**

```python
"""FileTab — TUI tab type for viewing and lightly editing files.

VIEW mode (default): syntax-highlighted read-only display with 2s mtime
polling. File changes on disk trigger an auto-reload.

EDIT mode (press `e`): TextEditor becomes writable. File changes on disk
show a warning bar with [r] reload / [k] keep options. Ctrl+S saves.
Esc exits edit mode (modifications are kept in memory until saved or
the tab is closed).
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Static, TextEditor

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
    FileTab TextEditor { height: 1fr; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        super().__init__(id=f"filetab-{abs(hash(str(self._path)))}")
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
        editor = TextEditor(id="ft-editor", read_only=True)
        if lang:
            editor.language = lang
        yield editor

    async def on_mount(self) -> None:
        try:
            content = self._path.read_text(errors="replace")
            self._mtime = self._path.stat().st_mtime
        except OSError:
            content = f"(could not read {self._path})"
        self.query_one("#ft-editor", TextEditor).load_text(content)
        self._refresh_status()
        self.set_interval(_MTIME_POLL_S, self._check_mtime)

    # --- status bar -------------------------------------------------

    def _refresh_status(self) -> None:
        mode = "EDIT" if self._edit_mode else "VIEW"
        mod = " *" if self._modified else ""
        try:
            loc = self.query_one("#ft-editor", TextEditor).cursor_location
            pos = f"  {loc.row + 1}:{loc.column + 1}"
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
            self.query_one("#ft-editor", TextEditor).load_text(content)
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
            self.query_one("#ft-editor", TextEditor).read_only = False
            self._refresh_status()

    def key_r(self) -> None:
        if self._disk_changed:
            try:
                content = self._path.read_text(errors="replace")
                self._mtime = self._path.stat().st_mtime
                self.query_one("#ft-editor", TextEditor).load_text(content)
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
            self.query_one("#ft-editor", TextEditor).read_only = True
            self._refresh_status()

    def on_text_editor_changed(self, _event: TextEditor.Changed) -> None:
        if self._edit_mode:
            self._modified = True
            self._refresh_status()

    async def action_save(self) -> None:
        if not self._edit_mode:
            return
        try:
            content = self.query_one("#ft-editor", TextEditor).text
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
            self.query_one("#ft-editor", TextEditor).focus()

    async def close(self) -> None:
        pass  # No async teardown needed
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_file_tab.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/file_tab.py tests/test_file_tab.py
git commit -m "feat(tui): add FileTab — file viewer/editor tab"
```

---

## Task 4: App integration (Ctrl+O + tab management)

**Files:**
- Modify: `src/aegis/tui/app.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_file_tab.py`:

```python
@pytest.mark.asyncio
async def test_app_deduplicates_file_tabs(tmp_path: Path):
    """Opening the same path twice focuses the existing tab."""
    from aegis.tui.file_tab import FileTab
    f = tmp_path / "dup.py"
    f.write_text("x = 1")
    tab1 = FileTab(f)
    tab2 = FileTab(f)
    # Same resolved path → same tab ID
    assert tab1.id == tab2.id
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_file_tab.py::test_app_deduplicates_file_tabs -v
```
Expected: PASS (tab ID is derived from path hash, same path → same ID).

- [ ] **Step 3: Add Ctrl+O binding and file-tab methods to AegisApp**

In `src/aegis/tui/app.py`, make these three changes:

**3a.** In the `BINDINGS` list, add after the existing `ctrl+d` binding:
```python
Binding("ctrl+o", "open_file_picker", "Open file", priority=True),
```

**3b.** Add `action_open_file_picker` method (place it near `action_pick_agent`):
```python
@work
async def action_open_file_picker(self, prefill: str = "") -> None:
    from aegis.tui.picker import FilePickerModal
    path = await self.push_screen_wait(FilePickerModal(prefill=prefill))
    if path is not None:
        await self._open_file_tab(path)
```

**3c.** Add `_open_file_tab` and `open_file` methods (place after `_spawn_terminal`):
```python
async def _open_file_tab(self, path: Path) -> None:
    from aegis.tui.file_tab import FileTab
    resolved = path.resolve()
    # Dedup: if a FileTab with same id already exists, focus it.
    tab_id = f"filetab-{abs(hash(str(resolved)))}"
    for p in self._panes:
        if p.id == tab_id:
            cs = self.query_one(ContentSwitcher)
            cs.current = tab_id
            p.unseen = False
            p.focus_input()
            self._refresh_tabbar()
            return
    tab = FileTab(resolved)
    self._panes.append(tab)
    cs = self.query_one(ContentSwitcher)
    await cs.mount(tab)
    cs.current = tab.id
    self._refresh_tabbar()
    tab.focus_input()

async def open_file(self, path: str) -> dict:
    """AppBridge entry point for aegis_view_file MCP tool."""
    try:
        resolved = Path(path).resolve()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    if not resolved.is_file():
        return {"status": "error", "reason": "file not found",
                "path": str(path)}
    tab_id = f"filetab-{abs(hash(str(resolved)))}"
    for p in self._panes:
        if p.id == tab_id:
            self.run_worker(self._focus_existing_tab(p),
                            group=f"focus-{tab_id}", exclusive=False)
            return {"status": "focused", "path": str(resolved)}
    self.run_worker(self._open_file_tab(resolved),
                    group=f"open-file-{tab_id}", exclusive=False)
    return {"status": "opened", "path": str(resolved)}

async def _focus_existing_tab(self, tab) -> None:
    cs = self.query_one(ContentSwitcher)
    cs.current = tab.id
    tab.unseen = False
    tab.focus_input()
    self._refresh_tabbar()
```

Also add `from pathlib import Path` to app.py imports if not already present (check — it already is, since `state_dir` uses it).

- [ ] **Step 4: Verify the app still starts**

```bash
python -c "from aegis.tui.app import AegisApp; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Also update _write_snapshot to skip FileTabs**

In `_write_snapshot`, the line:
```python
tabs = [_pane_to_tab(p, i) for i, p in enumerate(self._panes)
        if isinstance(p, ConversationPane)]
```
already filters to `ConversationPane` only — `FileTab` will be silently skipped. Verify `_write_snapshot` compiles without errors after your edits by importing:

```bash
python -c "from aegis.tui.app import AegisApp; print('ok')"
```

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/app.py
git commit -m "feat(tui): wire FileTab into AegisApp — Ctrl+O, dedup, open_file bridge"
```

---

## Task 5: Clickable backtick tokens in agent responses

**Files:**
- Modify: `src/aegis/tui/pane.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_file_picker.py`:

```python
def test_extract_backtick_tokens():
    from aegis.tui.pane import _extract_backtick_tokens
    assert _extract_backtick_tokens("see `foo.py` for details") == ["foo.py"]
    assert _extract_backtick_tokens("no backticks here") == []
    assert _extract_backtick_tokens("`a.py` and `b.py`") == ["a.py", "b.py"]
    assert _extract_backtick_tokens("") == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_file_picker.py::test_extract_backtick_tokens -v
```
Expected: `ImportError` — `_extract_backtick_tokens` not defined yet.

- [ ] **Step 3: Add helper and update CopyableBlock in pane.py**

**3a.** After the existing imports in `pane.py`, add:
```python
import re
```

**3b.** Add the helper function just before the `CopyableBlock` class definition (around line 152):
```python
def _extract_backtick_tokens(text: str) -> list[str]:
    """Return list of strings enclosed in single backticks."""
    return re.findall(r"`([^`\n]+)`", text)
```

**3c.** In `CopyableBlock.__init__`, change the tooltip line from:
```python
        self.tooltip = "click to copy"
```
to:
```python
        tokens = _extract_backtick_tokens(text_payload)
        self._backtick_tokens: list[str] = tokens
        self.tooltip = "click to open file" if tokens else "click to copy"
```

**3d.** In `CopyableBlock.update_content`, after updating `self._text_payload`, add:
```python
        tokens = _extract_backtick_tokens(text_payload)
        self._backtick_tokens = tokens
        self.tooltip = "click to open file" if tokens else "click to copy"
```

**3e.** Replace `CopyableBlock.on_click` with:
```python
    def on_click(self, event: Click) -> None:
        if self._backtick_tokens:
            import contextlib
            with contextlib.suppress(Exception):
                from aegis.tui.picker import FilePickerModal
                self.app.push_screen(
                    FilePickerModal(prefill=self._backtick_tokens[0]))
            return
        if not self._text_payload:
            return
        try:
            self.app.copy_to_clipboard(self._text_payload)
        except Exception:
            return
        try:
            self.app.notify(
                f"copied {len(self._text_payload)} chars", timeout=1.5)
        except Exception:
            pass
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_file_picker.py -v
pytest tests/test_pane_replay.py -v
```
Expected: all pass (pane replay tests should be unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_file_picker.py
git commit -m "feat(tui): clicking backtick tokens in agent responses opens file picker"
```

---

## Task 6: MCP tool — aegis_view_file

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Create: `tests/test_mcp_view_file.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_mcp_view_file.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.mcp.server import build_server


async def _call(server, name, **kwargs):
    """Mirror the helper in test_mcp_server.py."""
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


class _FakeBridge:
    queue_manager = None
    canvas_manager = MagicMock()
    terminal_manager = MagicMock()
    groups = MagicMock()
    remotes: dict = {}
    scheduler = None
    workflow_registry = MagicMock()

    def __init__(self, state_root: Path, *, has_open_file: bool = True) -> None:
        from aegis.queue import InboxRouter
        self.inbox_router = InboxRouter()
        self.state_root = state_root
        self.workflow_registry.get.return_value = None
        if has_open_file:
            self.open_file = AsyncMock(
                return_value={"status": "opened",
                              "path": str(state_root / "x.py")})

    def list_sessions(self): return []
    def list_agents(self): return []
    def inline_schedule_names(self): return set()
    async def handoff(self, a, b, c): return "ok"
    async def spawn(self, profile, *, handle=None): return "h"
    async def close(self, handle): pass


@pytest.mark.asyncio
async def test_aegis_view_file_calls_open_file(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("hello")
    bridge = _FakeBridge(tmp_path)
    server = build_server(bridge)
    result = await _call(server, "aegis_view_file", path=str(f))
    bridge.open_file.assert_called_once_with(str(f))
    assert result["status"] == "opened"


@pytest.mark.asyncio
async def test_aegis_view_file_no_tui(tmp_path: Path):
    bridge = _FakeBridge(tmp_path, has_open_file=False)
    server = build_server(bridge)
    result = await _call(server, "aegis_view_file",
                         path=str(tmp_path / "x.py"))
    assert result["status"] == "no_tui"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/test_mcp_view_file.py -v
```
Expected: FAIL — `aegis_view_file` tool not found.

- [ ] **Step 3: Add aegis_view_file to build_server in server.py**

In `src/aegis/mcp/server.py`, inside `build_server(bridge)`, add the new tool near the other `aegis_*` tools (e.g., after `aegis_list_agents`):

```python
    @server.tool
    async def aegis_view_file(path: str) -> dict:
        """Open a file in the Aegis TUI viewer tab.

        Opens a read-only syntax-highlighted view of the file. Press `e`
        in the tab to enter edit mode. If the TUI is not running (headless
        mode), returns status 'no_tui' without error.

        path: absolute or relative to the cwd where aegis was launched.
        """
        open_file = getattr(bridge, "open_file", None)
        if open_file is None:
            return {"status": "no_tui"}
        return await open_file(path)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_mcp_view_file.py -v
```
Expected: both tests pass.

- [ ] **Step 6: Run the full test suite to catch regressions**

```bash
pytest tests/ -x -q --timeout=30
```
Expected: all tests pass (or pre-existing failures only — none introduced by this feature).

- [ ] **Step 7: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_view_file.py
git commit -m "feat(mcp): add aegis_view_file tool — open file in TUI viewer"
```

---

## Task 7: Version bump and push

- [ ] **Step 1: Bump version in pyproject.toml**

In `pyproject.toml`, increment the patch version (e.g., `0.11.0` → `0.11.1` or whatever is current).

- [ ] **Step 2: Update CHANGELOG.md**

Add a section at the top:
```markdown
## [0.11.1] — 2026-05-26

### Added
- File viewer/editor tab (`FileTab`) — open any file with syntax highlighting
- Ctrl+O fuzzy file picker (`FilePickerModal`) with typeahead
- Backtick tokens in agent responses are clickable — opens file picker pre-filled
- MCP tool `aegis_view_file(path)` — agents can surface files to the operator
- 2-second mtime polling: VIEW mode auto-reloads, EDIT mode warns on external changes
```

- [ ] **Step 3: Final test run**

```bash
pytest tests/ -q --timeout=30
```
Expected: all tests pass.

- [ ] **Step 4: Final commit and push**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.11.1 — file viewer/editor"
git push
```
