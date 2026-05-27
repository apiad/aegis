# Aegis File Indexer + Picker UX Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blocking `rglob` in `FilePickerModal` with a background watchdog-indexed file list, and fix click behavior on `CopyableBlock` so click=copy and Ctrl+click=open-file-from-token.

**Architecture:** `FileIndexer` (new `tui/file_index.py`) starts an initial walk in a background thread on app mount, then an `Observer` (watchdog) keeps the list live. `FilePickerModal` reads from `app._file_indexer` and shows a "⏳ indexing…" placeholder if not ready yet. `CopyableBlock.on_click` reverts to copy; a new `@work` method handles Ctrl+click, routing through `_TokenChooser` if multiple tokens are present.

**Tech Stack:** Python 3.13, `watchdog>=4.0` (already a dep), Textual 8.x, `threading.Thread` + `threading.Event` for initial walk.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/aegis/tui/file_index.py` | **Create** | `FileIndexer` — background walk + watchdog live updates |
| `src/aegis/tui/picker.py` | Modify | Add `_TokenChooser`; update `FilePickerModal.on_mount` to use indexer |
| `src/aegis/tui/pane.py` | Modify | `CopyableBlock`: click=copy, Ctrl+click=open-from-tokens |
| `src/aegis/tui/app.py` | Modify | Wire `FileIndexer` into `__init__`, `on_mount`, `action_quit` |
| `tests/test_file_index.py` | **Create** | `FileIndexer` unit tests |
| `tests/test_file_picker.py` | Modify | Add `_TokenChooser` test; add indexer-integration picker test |

---

## Task 1: FileIndexer

**Files:**
- Create: `src/aegis/tui/file_index.py`
- Create: `tests/test_file_index.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_file_index.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

import pytest

from aegis.tui.file_index import FileIndexer


def _wait_ready(indexer: FileIndexer, timeout: float = 5.0) -> None:
    assert indexer._ready.wait(timeout), "indexer did not become ready"


def test_indexes_files(tmp_path: Path):
    (tmp_path / "foo.py").write_text("x")
    (tmp_path / "bar.md").write_text("y")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "foo.py" in idx.paths
    assert "bar.md" in idx.paths
    idx.stop()


def test_ignores_pyc(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x")
    (tmp_path / "bad.pyc").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "ok.py" in idx.paths
    assert "bad.pyc" not in idx.paths
    idx.stop()


def test_ignores_pycache_dir(tmp_path: Path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-313.pyc").write_text("x")
    (tmp_path / "real.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "real.py" in idx.paths
    assert not any("__pycache__" in p for p in idx.paths)
    idx.stop()


def test_ignores_venv_dir(tmp_path: Path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pip.py").write_text("x")
    (tmp_path / "main.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "main.py" in idx.paths
    assert not any(".venv" in p for p in idx.paths)
    idx.stop()


def test_filter_substring(tmp_path: Path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "beta.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    result = idx.filter("alp")
    assert "alpha.py" in result
    assert "beta.py" not in result
    idx.stop()


def test_watchdog_adds_file(tmp_path: Path):
    (tmp_path / "existing.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    # Create a new file after indexer is running.
    (tmp_path / "new_file.py").write_text("x")
    time.sleep(0.5)  # give watchdog time to fire
    assert "new_file.py" in idx.paths
    idx.stop()


def test_watchdog_removes_file(tmp_path: Path):
    f = tmp_path / "soon_gone.py"
    f.write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "soon_gone.py" in idx.paths
    f.unlink()
    time.sleep(0.5)
    assert "soon_gone.py" not in idx.paths
    idx.stop()


def test_ready_false_before_start():
    idx = FileIndexer()
    assert not idx.ready
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_file_index.py -v 2>&1 | tail -5
```
Expected: `ModuleNotFoundError: No module named 'aegis.tui.file_index'`

- [ ] **Step 3: Create src/aegis/tui/file_index.py**

```python
"""Background file indexer with watchdog live updates.

Walks ``cwd`` in a daemon thread on ``start()``, then registers a
watchdog ``Observer`` to keep the list current as files are created,
deleted, or moved. Uses its own ignore rules — does not parse
``.gitignore``.

Thread safety: ``_paths`` is replaced atomically (single assignment)
after initial walk. Incremental updates append/remove under ``_lock``.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", ".svn", ".hg",
    "__pycache__",
    ".venv", "venv", "env", ".env",
    "node_modules", ".next", ".nuxt",
    "dist", "build", "target", "vendor",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox",
    ".eggs",
    "htmlcov", "coverage",
    ".idea", ".vscode",
    ".aegis", ".claude",
    "__MACOSX",
})

_IGNORE_EXTS: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".pyd",
    ".class",
    ".so", ".dll", ".dylib",
    ".o", ".a", ".obj", ".lib",
    ".exe", ".bin", ".wasm",
    ".gcov",
})

_IGNORE_NAMES: frozenset[str] = frozenset({".DS_Store", ".coverage"})


def _ignore_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.endswith(".egg-info")


def _ignore_file(path: Path) -> bool:
    if path.name in _IGNORE_NAMES:
        return True
    if path.suffix in _IGNORE_EXTS:
        return True
    n = path.name
    return n.endswith(".min.js") or n.endswith(".min.css") or n.endswith(".map")


class FileIndexer:
    """Async file index — starts a background walk + watchdog observer."""

    def __init__(self) -> None:
        self._paths: list[str] = []
        self._cwd: Path | None = None
        self._observer: Observer | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    # --- public API -------------------------------------------------

    def start(self, cwd: Path) -> None:
        """Start background walk + watchdog. Returns immediately."""
        self._cwd = cwd.resolve()
        threading.Thread(target=self._walk, daemon=True).start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def paths(self) -> list[str]:
        with self._lock:
            return list(self._paths)

    def filter(self, text: str) -> list[str]:
        """Return up to 50 paths containing ``text`` (case-insensitive)."""
        needle = text.lower()
        with self._lock:
            snapshot = self._paths
        return [p for p in snapshot if needle in p.lower()][:50]

    # --- background walk --------------------------------------------

    def _walk(self) -> None:
        cwd = self._cwd
        assert cwd is not None
        paths: list[str] = []
        try:
            for root, dirs, files in os.walk(cwd):
                dirs[:] = [d for d in dirs if not _ignore_dir(d)]
                root_path = Path(root)
                for fname in files:
                    fp = root_path / fname
                    if not _ignore_file(fp):
                        try:
                            paths.append(str(fp.relative_to(cwd)))
                        except ValueError:
                            paths.append(str(fp))
        except PermissionError:
            pass
        paths.sort()
        with self._lock:
            self._paths = paths
        self._ready.set()
        self._start_observer()

    def _start_observer(self) -> None:
        cwd = self._cwd
        if cwd is None:
            return
        handler = _IndexHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(cwd), recursive=True)
        self._observer.start()

    # --- incremental updates (called from watchdog thread) ----------

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
        # Skip if any parent component is an ignored dir.
        parts = Path(rel).parts
        if any(_ignore_dir(p) for p in parts[:-1]):
            return
        with self._lock:
            if rel not in self._paths:
                self._paths.append(rel)
                self._paths.sort()

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


class _IndexHandler(FileSystemEventHandler):
    def __init__(self, indexer: FileIndexer) -> None:
        self._idx = indexer

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._idx._add(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._idx._remove(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._idx._remove(event.src_path)
            self._idx._add(event.dest_path)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_file_index.py -v 2>&1 | tail -15
```
Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/file_index.py tests/test_file_index.py
git commit -m "feat(tui): add FileIndexer — background walk + watchdog live index"
```

---

## Task 2: Wire FileIndexer into AegisApp

**Files:**
- Modify: `src/aegis/tui/app.py`

The `FileIndexer` must be created in `__init__`, started in `on_mount` (non-blocking), and stopped in `action_quit`.

- [ ] **Step 1: Add `_file_indexer` to `AegisApp.__init__`**

In `src/aegis/tui/app.py`, inside `AegisApp.__init__`, add after the line `self._state_dir: Path = state_dir(Path.cwd())`:

```python
        from aegis.tui.file_index import FileIndexer
        self._file_indexer = FileIndexer()
```

- [ ] **Step 2: Start indexer in `on_mount`**

In `AegisApp.on_mount`, add this line immediately after `await self._mcp.start()`:

```python
        self._file_indexer.start(Path.cwd())
```

- [ ] **Step 3: Stop indexer in `action_quit`**

In `AegisApp.action_quit`, add before `self.exit()`:

```python
        self._file_indexer.stop()
```

- [ ] **Step 4: Verify import**

```bash
uv run python -c "from aegis.tui.app import AegisApp; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Run existing TUI tests**

```bash
uv run pytest tests/test_tui.py -v -q 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/app.py
git commit -m "feat(tui): wire FileIndexer into AegisApp startup/shutdown"
```

---

## Task 3: FilePickerModal reads from FileIndexer

**Files:**
- Modify: `src/aegis/tui/picker.py`
- Modify: `tests/test_file_picker.py`

Replace the blocking `rglob` in `FilePickerModal.on_mount` with a read from `app._file_indexer`. If the indexer isn't ready yet, show a placeholder and poll.

- [ ] **Step 1: Write failing test**

Add to `tests/test_file_picker.py`:

```python
@pytest.mark.asyncio
async def test_file_picker_uses_indexer(tmp_path: Path):
    """Picker reads from app._file_indexer when available."""
    from aegis.tui.file_index import FileIndexer

    (tmp_path / "indexed.py").write_text("x")

    class _AppWithIndexer(App):
        def __init__(self) -> None:
            super().__init__()
            self._file_indexer = FileIndexer()
            self._file_indexer.start(tmp_path)
            self._file_indexer._ready.wait(timeout=3)

        def compose(self) -> ComposeResult:
            yield FilePickerModal()

    app = _AppWithIndexer()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import OptionList
        ol = app.query_one("#fp-list", OptionList)
        # Give time for the filter to populate from the indexer
        await pilot.pause()
        option_ids = [ol.get_option_at_index(i).id
                      for i in range(ol.option_count)]
        assert any("indexed.py" in (oid or "") for oid in option_ids)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_file_picker.py::test_file_picker_uses_indexer -v 2>&1 | tail -10
```
Expected: FAIL — picker still does `rglob`, not reading from the indexer.

- [ ] **Step 3: Replace `FilePickerModal.on_mount`**

In `src/aegis/tui/picker.py`, replace the entire `on_mount` method of `FilePickerModal` (currently lines that do `cwd.rglob(...)`) with:

```python
    def on_mount(self) -> None:
        # Try to read from the app-level FileIndexer (fast path).
        indexer = getattr(self.app, "_file_indexer", None)
        if indexer is not None and indexer.ready:
            self._all_paths = indexer.paths
            self._boot_input()
        elif indexer is not None:
            # Not ready yet — show placeholder and poll.
            ol = self.query_one("#fp-list", OptionList)
            ol.add_option(Option("⏳ indexing files…", id=None))
            self.set_interval(0.15, self._poll_indexer)
            self.query_one("#fp-input", Input).focus()
        else:
            # Fallback: no indexer (e.g. unit tests without full app).
            self._sync_walk()
            self._boot_input()

    def _boot_input(self) -> None:
        inp = self.query_one("#fp-input", Input)
        if self._prefill:
            inp.value = self._prefill
        inp.focus()
        self._filter(self._prefill)

    def _poll_indexer(self) -> None:
        indexer = getattr(self.app, "_file_indexer", None)
        if indexer is None or not indexer.ready:
            return
        self._all_paths = indexer.paths
        self._boot_input()
        # Cancel the polling interval by removing the timer.
        # Textual: timers started with set_interval can't be cancelled
        # directly; we guard by checking ready again on each call.
        # The timer keeps firing but is a no-op after the first hit.

    def _sync_walk(self) -> None:
        """Fallback synchronous walk (used when no FileIndexer is attached)."""
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
```

- [ ] **Step 4: Run all picker tests**

```bash
uv run pytest tests/test_file_picker.py -v 2>&1 | tail -15
```
Expected: all tests pass (including old ones which run without a full `AegisApp`, falling through to `_sync_walk`).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/picker.py tests/test_file_picker.py
git commit -m "feat(tui): FilePickerModal reads from FileIndexer — instant open"
```

---

## Task 4: _TokenChooser + Ctrl+click on CopyableBlock

**Files:**
- Modify: `src/aegis/tui/picker.py` — add `_TokenChooser`
- Modify: `src/aegis/tui/pane.py` — fix `CopyableBlock` click behavior
- Modify: `tests/test_file_picker.py` — test `_TokenChooser`

- [ ] **Step 1: Write failing test for _TokenChooser**

Add to `tests/test_file_picker.py`:

```python
@pytest.mark.asyncio
async def test_token_chooser_returns_selected():
    result_holder: list = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            self.push_screen(
                _TokenChooser(["src/foo.py", "tests/bar.py"]),
                callback=lambda r: result_holder.append(r) or self.exit())

    from aegis.tui.picker import _TokenChooser
    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Select first option
        await pilot.press("enter")
        for _ in range(5):
            await pilot.pause()

    assert result_holder and result_holder[0] == "src/foo.py"


@pytest.mark.asyncio
async def test_token_chooser_escape_returns_none():
    result_holder: list = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            self.push_screen(
                _TokenChooser(["a.py", "b.py"]),
                callback=lambda r: result_holder.append(r) or self.exit())

    from aegis.tui.picker import _TokenChooser
    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()

    assert result_holder == [None]
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_file_picker.py::test_token_chooser_returns_selected -v 2>&1 | tail -5
```
Expected: `ImportError` — `_TokenChooser` not defined.

- [ ] **Step 3: Add _TokenChooser to picker.py**

At the end of `src/aegis/tui/picker.py`, append:

```python
class _TokenChooser(ModalScreen):
    """Pick one backtick token from a list — routes to FilePickerModal."""

    DEFAULT_CSS = """
    _TokenChooser { align: center middle; }
    _TokenChooser OptionList {
        width: 50; max-height: 16;
        border: round $panel; background: $surface;
    }
    """

    def __init__(self, tokens: list[str]) -> None:
        super().__init__()
        self._tokens = tokens

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(t, id=t) for t in self._tokens])

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def key_escape(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Run TokenChooser tests**

```bash
uv run pytest tests/test_file_picker.py::test_token_chooser_returns_selected tests/test_file_picker.py::test_token_chooser_escape_returns_none -v 2>&1 | tail -8
```
Expected: both pass.

- [ ] **Step 5: Fix CopyableBlock click behavior in pane.py**

Replace the entire `CopyableBlock` `__init__`, `update_content`, `on_click` methods and add `_open_file_from_tokens`:

**`__init__`** — change tooltip:
```python
    def __init__(self, renderable: RenderableType,
                 text_payload: str, *, tight: bool = False) -> None:
        super().__init__(classes="-tight" if tight else None)
        self._renderable = renderable
        self._text_payload = text_payload
        self._backtick_tokens: list[str] = _extract_backtick_tokens(
            text_payload)
        self.tooltip = (
            "click to copy | ctrl+click to open file"
            if self._backtick_tokens else "click to copy"
        )
```

**`update_content`** — update tooltip consistently:
```python
    def update_content(self, renderable: RenderableType,
                       text_payload: str) -> None:
        self._renderable = renderable
        self._text_payload = text_payload
        self._backtick_tokens = _extract_backtick_tokens(text_payload)
        self.tooltip = (
            "click to copy | ctrl+click to open file"
            if self._backtick_tokens else "click to copy"
        )
        with contextlib.suppress(Exception):
            self.query_one(".content", Static).update(renderable)
```

**`on_click`** — restore copy, delegate Ctrl+click:
```python
    def on_click(self, event: Click) -> None:
        if event.ctrl and self._backtick_tokens:
            self._open_file_from_tokens()
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

**Add `_open_file_from_tokens` after `on_click`:**
```python
    @work
    async def _open_file_from_tokens(self) -> None:
        tokens = self._backtick_tokens
        if len(tokens) == 1:
            token = tokens[0]
        else:
            from aegis.tui.picker import _TokenChooser
            token = await self.app.push_screen_wait(_TokenChooser(tokens))
            if token is None:
                return
        from aegis.tui.picker import FilePickerModal
        self.app.push_screen(FilePickerModal(prefill=token))
```

Also add `from textual import work` to the imports at the top of `pane.py` if not already present. Check with:

```bash
grep "^from textual import" /home/apiad/Workspace/repos/aegis/src/aegis/tui/pane.py
```

If not present, add it alongside the existing textual imports.

- [ ] **Step 6: Run all picker and pane tests**

```bash
uv run pytest tests/test_file_picker.py tests/test_pane_replay.py -v -q 2>&1 | tail -10
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/tui/picker.py src/aegis/tui/pane.py tests/test_file_picker.py
git commit -m "feat(tui): Ctrl+click opens file token chooser; click restores copy"
```

---

## Task 5: Full test suite + version bump

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full suite**

```bash
uv run pytest tests/ -q -m "not live" 2>&1 | tail -10
```
Expected: all pass, 0 failures.

- [ ] **Step 2: Bump version**

In `pyproject.toml`, change `version = "0.11.1"` to `version = "0.11.2"`.

- [ ] **Step 3: Update CHANGELOG**

Add at the top of `CHANGELOG.md` after `## [Unreleased]`:

```markdown
## [0.11.2] - 2026-05-26

### File picker improvements

- Background `FileIndexer` (watchdog + `os.walk`) starts on app load — picker
  opens instantly instead of blocking on `rglob`. Ships its own comprehensive
  ignore list (`.git`, `__pycache__`, `.venv`, `node_modules`, `*.pyc`, etc.);
  does not parse `.gitignore`. Live-updates as agents create or delete files.
- `FilePickerModal` reads from `FileIndexer` when available; falls back to
  synchronous walk in test environments without a full `AegisApp`.
- `CopyableBlock`: click = copy text (restored); ctrl+click = open file from
  backtick token. Multiple tokens → `_TokenChooser` lets you pick which one.
  Tooltip updated to `"click to copy | ctrl+click to open file"` when tokens
  are present.
```

- [ ] **Step 4: Commit and push**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.11.2 — file indexer + picker UX fixes"
git push
```
