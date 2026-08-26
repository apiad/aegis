# File Browser Tab — Design

**Date:** 2026-08-20  
**Status:** implemented (`240c415`…`0b010a4`, 2026-08-20)

## Summary

Replace the `Ctrl+O` modal file picker with a first-class **FileBrowserTab** —
a dedicated TUI tab that combines a recency-sorted file list, a fuzzy filter
input, a full editor (same power as `FileTab`), and a toggleable file-tree
sidebar on the right. Each `Ctrl+O` opens a new tab; multiple can coexist.

---

## Motivation

The current `Ctrl+O` flow opens `FilePickerModal` — a fullscreen modal that
dismisses as soon as a file is chosen. There is no persistent browser, no tree
view, and no recency ordering. The most common use case — "open whatever the
agent just touched" — requires knowing the filename up front. The browser tab
fixes all three gaps.

---

## Architecture

Three additive changes:

1. **Mtime layer on `FileIndexer`** — extend the existing indexer to track
   file modification times, exposed via a new `paths_by_mtime()` accessor.
2. **`FileBrowserTab`** (new file `tui/file_browser_tab.py`) — the composite
   tab type.
3. **`Ctrl+O` wiring change in `app.py`** — create a `FileBrowserTab` instead
   of pushing `FilePickerModal`.

The existing `FilePickerModal` is **not changed** — the ctrl+click
backtick-token flow in `pane.py` continues to use it.

---

## 1. Mtime layer on FileIndexer

### Current state

`FileIndexer._paths` is a `list[str]` sorted alphabetically. The walk and
watchdog handler already visit every file. There is no mtime tracking.

### Change

Add a parallel `_mtimes: dict[str, float]` mapping relative path → mtime. The
walk populates it via `os.stat(full).st_mtime`; the incremental `_add` handler
does the same on created/moved files; `_remove` deletes the entry.

New public method:

```python
def paths_by_mtime(self) -> list[str]:
    """Files sorted by mtime descending (most recently touched first)."""
```

Returns a snapshot (copy), sorted descending by `_mtimes[p]`. Files absent
from `_mtimes` (e.g. before the first walk completes) sort last. Thread-safe:
takes `_lock`.

**Nothing else changes.** `paths`, `filter()`, `ready`, `start`, `stop` are
untouched.

---

## 2. FileBrowserTab

### Tab contract

`FileBrowserTab` satisfies the same tab contract as `ConversationPane` and
`FileTab`:

| attribute | value |
|---|---|
| `handle` | `"browser:<n>"` (counter suffix so multiple tabs are distinct) |
| `agent_slug` | `"browser"` |
| `state` | `AgentState.ready` always |
| `unseen` | `False` always |
| `focus_input()` | focuses the filter input (browse) or TextArea (view) |
| `set_sidebar_mode(open)` | shows/hides the tree panel |
| `close()` | no-op (no subprocess) |

### Layout

```
┌── left column (1fr) ──┬── tree sidebar (28 cells, hideable) ──┐
│                       │                                        │
│  [browse mode]        │  DirectoryTree                         │
│  Filter: ________     │  rooted at project root                │
│                       │                                        │
│  2s   src/aegis/…     │  ▾ src/                               │
│  5s   src/aegis/…     │    ▾ aegis/                           │
│  1m   tests/test_…    │        tui/                           │
│  …                    │          app.py                        │
│                       │          file_tab.py                   │
│  [view mode]          │                                        │
│  status bar           │                                        │
│  TextArea             │                                        │
│                       │                                        │
└───────────────────────┴────────────────────────────────────────┘
```

The sidebar width is 28 cells (matching existing sidebar proportions). The left
column is a `ContentSwitcher` toggling between the browse child and the view
child.

### Browse mode

Widgets:
- `Input` — filter text box, auto-focused on mount
- `OptionList` / `ListView` — the file list

On mount and on every file-system change (driven by a 2-second poll of
`indexer.paths_by_mtime()`), the list is repopulated. Each entry label:

```
<age>   <rel-path>
```

Age is a human-readable relative time derived from `time.time() - mtime`:
`<N>s`, `<N>m`, `<N>h`, `<N>d`. Capped at `>30d` for very old files.

Typing in the filter runs `indexer.filter(text)` (existing fuzzy match, case-
insensitive substring) over the mtime-sorted snapshot and re-renders the list
in the same mtime order (filter narrows, does not re-sort alphabetically).

`Enter` or click on an entry → switch to view mode for that file.

### View mode

An embedded editor with the same capabilities as the existing `FileTab`:

- Syntax-highlighted `TextArea` (language from extension, same `_EXT_LANGUAGE`
  map)
- Read-only by default; `e` enters edit mode; `Ctrl+S` saves; `Esc` exits edit
  mode (with unsaved-edits confirm bar if dirty)
- 2-second mtime poll — auto-reload when not editing, disk-changed warning bar
  when editing
- `p` toggles Markdown preview (`.md` files only)
- `Ctrl+X` opens in external editor (`xdg-open`)
- Status bar: `<path>  [VIEW|EDIT|PREVIEW]*  <line>:<col>`

**The editor logic is not copy-pasted from `FileTab`.** `FileBrowserTab`
instantiates an internal `FileTab` widget and mounts it inside the view
content-switcher child. This keeps the two in sync as `FileTab` evolves.

`b` (or `Esc` when in VIEW/PREVIEW mode, not EDIT mode) switches back to
browse mode. The filter input is restored to its previous value and the list
re-focuses on the file that was open.

### Sidebar — file tree

`DirectoryTree` from `textual.widgets`, rooted at `find_project_root()` (same
function used elsewhere in aegis to locate the project). Falls back to `cwd`
if no `.aegis.yaml` is found above `cwd`.

`DirectoryTree.FileSelected` → open the selected path in view mode.

The sidebar column has `display: none` when the app's sidebar mode is closed,
`display: block` when open. `set_sidebar_mode(open: bool)` sets this.

### F3 integration

`AegisApp.set_sidebar_mode` already fans the call out to every pane in
`_panes`. `FileBrowserTab` is appended to `_panes` like any other tab, so it
participates automatically — no new fan-out code needed, only the
`set_sidebar_mode` method on the tab itself.

The tab adopts the current app sidebar mode in `on_mount` (same pattern as
`ConversationPane`).

---

## 3. Ctrl+O wiring

`action_open_file_picker` currently:

```python
path = await self.push_screen_wait(FilePickerModal(prefill=prefill))
if path is not None:
    await self._open_file_tab(path)
```

New:

```python
async def action_open_file_picker(self, prefill: str = "") -> None:
    from aegis.tui.file_browser_tab import FileBrowserTab
    tab = FileBrowserTab(
        cwd=self._file_indexer._cwd or Path.cwd(),
        indexer=self._file_indexer,
        prefill=prefill,
        sidebar_open=self.sidebar_mode,
    )
    self._panes.append(tab)
    cs = self.query_one(ContentSwitcher)
    tab.display = False
    await cs.mount(tab)
    cs.current = tab.id
    self._refresh_tabbar()
    tab.focus_input()
```

The `prefill` parameter is threaded through so existing call sites that pass a
pre-filled path string (e.g. ctrl+click with a token) can still pre-populate
the filter. When `prefill` names an existing file, the browser tab opens
directly in view mode for that file.

---

## Files touched

| file | change |
|---|---|
| `src/aegis/tui/file_index.py` | add `_mtimes`, `paths_by_mtime()`, mtime capture in `_walk`/`_add`/`_remove` |
| `src/aegis/tui/file_browser_tab.py` | **new** — `FileBrowserTab` |
| `src/aegis/tui/app.py` | change `action_open_file_picker` |
| `tests/test_file_browser_tab.py` | **new** — unit tests |
| `tests/test_file_index.py` | extend — mtime accessor tests |

`picker.py`, `file_tab.py`, `pane.py`, `sidebar.py` are **not changed**.

---

## Testing

`tests/test_file_browser_tab.py`:

- Browse mode renders files sorted by mtime (most recent first)
- Typing in filter narrows list (substring match, mtime order preserved)
- Selecting a file switches to view mode (FileTab mounted)
- `b` / `Esc` in VIEW returns to browse
- `set_sidebar_mode(True/False)` shows/hides tree column
- `prefill` with an existing path opens directly in view mode
- `prefill` with a non-existent path opens browse with filter pre-filled

`tests/test_file_index.py`:

- `paths_by_mtime()` returns most-recently-modified file first
- After `_remove`, path absent from mtime sort
- After `_add`, path present with correct mtime

---

## Deferred

- Tree node highlight tracking the currently-open file in view mode (requires
  `DirectoryTree` API that may need probing; not blocking)
- Per-browser-tab history (back/forward through viewed files within the tab)
- Web client parity (no `DirectoryTree` equivalent in the PWA yet)
