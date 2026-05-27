# Aegis File Indexer + Picker UX Fixes — Design

**Date:** 2026-05-26
**Status:** Approved

## Overview

Two improvements to the file viewer shipped in v0.11.1:

1. **Background file indexer** — async watchdog-based index so `FilePickerModal` opens instantly instead of blocking on `rglob`. Ships its own ignore list; does not parse `.gitignore`.
2. **Ctrl+click per-token targeting** — restore `click = copy` on `CopyableBlock`; `Ctrl+click` opens a token chooser (if multiple backtick tokens) or directly opens the file picker (if one).

---

## Feature 1: Background File Indexer

### Architecture

A `FileIndexer` class (`tui/file_index.py`) wraps a watchdog `Observer`. It:
- Walks `cwd` at startup in a background thread.
- Maintains a sorted `list[str]` of relative paths (thread-safe reads via GIL).
- Reacts to `created`, `deleted`, `moved` filesystem events to keep the index live.
- Is started once in `AegisApp.on_mount` (non-blocking) and stopped in `action_quit`.
- `FilePickerModal` reads `app._file_indexer.paths` on open instead of doing its own `rglob`.

### Ignore Rules (shipped, not from .gitignore)

**Directory names (skip entire subtree):**
`.git`, `.svn`, `.hg`, `__pycache__`, `.venv`, `venv`, `env`, `.env`,
`node_modules`, `.next`, `.nuxt`, `dist`, `build`, `target`, `vendor`,
`.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.tox`, `.nox`,
`.eggs`, `.egg-info`, `htmlcov`, `coverage`, `.idea`, `.vscode`,
`.aegis`, `.claude`, `__MACOSX`

**File extensions (skip file):**
`.pyc`, `.pyo`, `.pyd`, `.class`, `.so`, `.dll`, `.dylib`,
`.o`, `.a`, `.obj`, `.lib`, `.exe`, `.bin`, `.wasm`,
`.DS_Store`, `.coverage`, `.gcov`

**File name patterns (skip file):**
`*.min.js`, `*.min.css`, `*.map` (source maps)

### API

```python
class FileIndexer:
    def start(self, cwd: Path) -> None: ...   # starts background walk + watcher
    def stop(self) -> None: ...               # stops watcher
    @property
    def paths(self) -> list[str]: ...         # snapshot of current index
    def filter(self, text: str) -> list[str]: ...  # substring filter, top-50
    @property
    def ready(self) -> bool: ...              # True once initial walk completes
```

### Integration Points

- `AegisApp.__init__`: `self._file_indexer = FileIndexer()`
- `AegisApp.on_mount`: `self._file_indexer.start(Path.cwd())`
- `AegisApp.action_quit`: `self._file_indexer.stop()`
- `FilePickerModal.on_mount`: reads `app._file_indexer.paths` instead of `rglob`. If indexer not ready, shows "indexing…" placeholder and polls via `set_interval` until ready.

---

## Feature 2: Ctrl+Click Per-Token Targeting

### Behavior change

| Gesture | Old | New |
|---------|-----|-----|
| Click on block | opens picker (if backtick tokens) or copies | copies text to clipboard |
| Ctrl+click on block (has 1 token) | — | opens `FilePickerModal(prefill=token)` |
| Ctrl+click on block (has N>1 tokens) | — | opens `_TokenChooser` listing all tokens, then opens `FilePickerModal(prefill=chosen)` |
| Ctrl+click on block (no tokens) | — | no-op (or copy) |

### _TokenChooser

A minimal `ModalScreen[str | None]` (same pattern as `AgentPicker`) with an `OptionList` of all backtick token strings from the block. Dismisses with the chosen token or `None` on Escape. Lives in `tui/picker.py`.

### Tooltip

- Block has tokens: `"click to copy | ctrl+click to open file"`
- Block has no tokens: `"click to copy"`

### Implementation

In `CopyableBlock.on_click`:
```python
def on_click(self, event: Click) -> None:
    if event.ctrl and self._backtick_tokens:
        self._open_file_from_tokens()
        return
    # copy behavior (restored)
    ...

@work
async def _open_file_from_tokens(self) -> None:
    tokens = self._backtick_tokens
    if len(tokens) == 1:
        token = tokens[0]
    else:
        token = await self.app.push_screen_wait(_TokenChooser(tokens))
        if token is None:
            return
    self.app.push_screen(FilePickerModal(prefill=token))
```

Note: `_open_file_from_tokens` must be a `@work` worker because `push_screen_wait` requires a worker context.
