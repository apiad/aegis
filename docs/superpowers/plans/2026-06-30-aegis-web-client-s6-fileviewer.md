# Aegis Web Client — S6 (File Viewer + Picker) Implementation Plan

> superpowers:executing-plans (inline). Builds on S5.

**Goal:** A file picker (Alt+P) over the project tree and a viewer that renders **markdown** (via the client `markdown.js`), renders **HTML** natively (sandboxed iframe), and shows **everything else as source**.

**Architecture:** Reuse the existing `aegis.tui.file_index.FileIndexer` (pure file-walker + watchdog) — the web frontend runs its own, rooted at the served project. Two RPCs: `file_search{query}` (indexer filter) and `file_read{path}` (path-safe read + kind detection). The frontend picker fuzzy-lists paths; the viewer modal renders by kind.

## Global Constraints
- Build on S5; tests stay green. No new deps (watchdog already used by FileIndexer). Read-only.
- **Path safety:** `file_read` resolves within the project root and rejects traversal; caps size; reads `utf-8` with `errors="replace"`.
- **HTML render is sandboxed** (`<iframe sandbox srcdoc>`, no scripts) — never inject file HTML into the main document.
- Keyboard: **Alt+P** (Ctrl+P is browser print).
- Config panel (F2) is **deferred** to a later sub-slice — this slice is the viewer + picker.
- Commit to **main**; conventional commits.

## Tasks

### Task 1 — file_search + file_read RPCs (backend)
- `SubscriptionRegistry`: `set_files(indexer, root)`, `file_search(query) -> list[str]` (indexer.filter, or `paths[:50]` when empty), `file_read(path) -> dict`:
  - resolve `(root / path).resolve()`, require `.relative_to(root)` (else `{"error": "path outside project"}`);
  - not a file → error; size > 2 MB → error;
  - `kind`: `.md/.markdown` → `markdown`, `.html/.htm` → `html`, else `source`;
  - return `{"path", "kind", "content"}`.
  - No indexer/root → empty list / `{"error": "files unavailable"}`.
- `server.py build_web_app(..., files_root: Path | None = None)`: when set, `idx = FileIndexer(); idx.start(files_root); registry.set_files(idx, files_root.resolve())`. `WebFrontend` passes the project cwd; tests pass None.
- `WSSession._call`: `file_search` → `{"paths": self._reg.file_search(params.get("query",""))}`; `file_read` → `self._reg.file_read(params["path"])`.
- Test `tests/test_web_files.py`: build a tmp tree (a.md, b.html, c.py); a registry with a FileIndexer over tmp (call `_walk` synchronously or set `_paths` directly + root); assert `file_search` finds them; `file_read` returns correct kind + content for each; traversal (`../etc`) → error; missing → error. Drive `file_read`/`file_search` directly on the registry (sync) + one WSSession rpc round-trip.

### Task 2 — file picker + viewer (frontend)
- `app.js`:
  - `openFilePicker()` (Alt+P): modal with a search `<input>` + results `<div>`. On input (debounced ~120ms) → `rpc file_search` → list path rows; Enter/click first/selected → `openFileViewer(path)`. Initially shows `file_search("")` (first 50).
  - `openFileViewer(path)`: `rpc file_read` → a large modal: header (path), body by kind —
    - `markdown` → `div.assistant-text` with `innerHTML = renderMarkdown(content)`;
    - `html` → `<iframe sandbox srcdoc=content>` (native render, no scripts);
    - `source` → `<pre class="file-source">` with `textContent = content`;
    - error → muted message.
  - `wireKeys`: Alt+P → openFilePicker.
- `css`: `.file-modal` (wide/tall), `.file-result` (hover), `.file-source` (monospace pre), `.file-frame` (iframe fill).
- Acceptance: `node --check` + route tests green. DOM via smoke.

### Task 3 — visual smoke
Launch `aegis web` in a dir with a `.md`, an `.html`, and a `.py`. Alt+P → search → open each: confirm the md renders (bold/headers), the html renders in the frame, the py shows as source. Screenshot. (saidkick reconnected.)

## Self-Review
**Coverage:** RPCs + path safety (T1), picker + 3-way viewer (T2), end-to-end (T3). **Deferred (documented):** config panel (F2) — separate sub-slice; syntax highlighting of source (plain `<pre>` in S6; client highlighter later); `Ctrl+X`/xdg-open (localhost-only, later). **Security:** file HTML only ever in a sandboxed iframe; path traversal rejected; size capped.
