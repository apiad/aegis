# Aegis File Viewer/Editor — Design

**Date:** 2026-05-26
**Status:** Approved

## Overview

Add a `FileTab` — a new tab type alongside `AgentTab` — that displays any file with syntax highlighting and optional lightweight editing. Files are opened via a fuzzy file picker (Ctrl+O), by clicking backtick-wrapped filenames in agent responses, or by agents via an MCP tool.

Textual's built-in `TextEditor` widget handles buffer management and syntax highlighting; no custom editor logic is needed.

## Entry Points

Three ways to open a file:

1. **Ctrl+O** — opens `FilePickerModal` with empty input.
2. **Click on backtick token in agent response** — opens `FilePickerModal` pre-filled with the token text.
3. **MCP tool `aegis_view_file(path)`** — opens `FileTab` directly at the resolved path.

## FilePickerModal

A `ModalScreen` (following the existing `AgentPicker` / `TerminalNamePrompt` pattern in `tui/picker.py`) composed of:

- `Input` field at top — fuzzy typeahead, pre-fillable with a string.
- `OptionList` below — results filtered and ranked as the user types.

**Filtering:** walk the filesystem from cwd on mount (bounded to ~5000 entries), store paths in memory, filter on each `Input.Changed` event using simple substring or `difflib.get_close_matches`. No external fuzzy library needed.

**Selection:** Enter on a highlighted option → dismiss with the selected path → caller opens `FileTab`. Escape → dismiss with `None`.

**Pre-fill:** caller passes an optional `prefill: str` to `__init__`; on mount the Input is set to that value and filtering runs immediately.

## FileTab

A new tab type rendered inside a `ContentSwitcher` slot, alongside existing agent/terminal/dashboard tabs. The tab label shows the filename (basename, truncated to 20 chars with `…`).

**Composition:**
- `StatusBar` at top — shows full resolved path, line/col, mode badge (`VIEW` or `EDIT`), modified flag (`*`).
- `TextEditor` (Textual built-in) filling the remaining space — syntax language auto-detected from file extension.

**State on open:** content loaded from disk, `TextEditor.read_only = True` (VIEW mode).

**Keybindings (active when FileTab is focused):**

| Key | Action |
|-----|--------|
| `e` | Enter EDIT mode (`read_only = False`) |
| `Ctrl+S` | Save to disk, clear modified flag (EDIT mode only) |
| `Escape` | Exit EDIT mode; prompt if unsaved changes |
| `Ctrl+W` / middle-click tab | Close tab; prompt if unsaved changes |

**Unsaved-changes prompt:** a simple `ModalScreen` with "Save / Discard / Cancel" options, reusing existing modal patterns.

**Deduplication:** if `aegis_view_file` or the picker resolves to a path already open in a FileTab, focus that tab instead of opening a duplicate.

## Clickable File Mentions in Agent Responses

In `tui/pane.py`, the response renderer wraps every backtick-delimited token in a Textual `@click` handler. On click: `app.push_screen(FilePickerModal(prefill=token_text))`.

No path validation at render time — if the token isn't a real path the picker simply shows no results.

**Scope:** backtick tokens only (`` `filename` `` syntax). Do not attempt to detect bare file paths in prose — too fragile.

## MCP Tool: `aegis_view_file`

Registered in `mcp/` alongside existing tools.

```
Tool: aegis_view_file
Args:
  path: str  — absolute or relative to cwd
Returns:
  {status: "opened" | "focused", path: "<resolved absolute>"}
```

- Resolves path relative to cwd; returns an error if the file does not exist.
- Posts a message to the TUI app to open or focus the `FileTab`.
- If the TUI is not running (headless mode), returns `{status: "no_tui"}` without error.

## File Change Detection

`FileTab` stores the file's `mtime` on open and polls it every 2 seconds via Textual's `set_interval`.

**VIEW mode:** mtime changed → silently reload content from disk, update stored mtime. Cursor resets to top.

**EDIT mode:** mtime changed → show a persistent warning in the status bar ("⚠ file changed on disk — [r] reload / [k] keep mine"). Do not auto-reload. Pressing `r` discards edits and reloads; pressing `k` (or continuing to type) dismisses the warning and keeps the in-memory content. On `Ctrl+S`, always write — no merge, last write wins.

**On save:** update stored mtime to the post-write value to suppress a spurious change notification.

## Implementation Notes

- `FilePickerModal` lives in `tui/picker.py` alongside `AgentPicker` and `TerminalNamePrompt`.
- `FileTab` lives in a new `tui/file_tab.py`.
- The click handler in `pane.py` reuses the existing `Click` event import.
- MCP tool registration follows the pattern in `mcp/server.py` (all existing `aegis_*` tools live there).
- Textual's `TextEditor` requires the `textual[syntax]` extra for tree-sitter highlighting. Current `pyproject.toml` only declares `textual>=8.2.6` — update to `textual[syntax]>=8.2.6` as part of this implementation.
