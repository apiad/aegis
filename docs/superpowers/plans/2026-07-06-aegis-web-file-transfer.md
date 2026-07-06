# Aegis Web File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bidirectional file transfer in the web/PWA client — upload a file to
the agent, and let the agent hand a file back as a clickable download.

**Architecture:** Two path-safe HTTP routes (`GET /download`, `POST /upload`)
on the existing Starlette app; a new `aegis_offer_file` MCP tool whose transcript
tool-event the web renderer special-cases into a download bubble; a composer
attach control that uploads to a per-session scratch dir and weaves the paths
into the delivered message. All additive.

**Tech Stack:** Starlette (routes, `FileResponse`, multipart via
`python-multipart`), FastMCP tool, vanilla ES modules, pytest + node `.mjs`.

## Global Constraints

- Python **3.13+**, `uv`. Python tests: `uv run python -m pytest -q -m "not live"`
  (`python -m pytest`, not bare `uv run pytest`). Never pipe pytest into `tail`.
- JS tests: dependency-free node, `node tests/web/<name>.test.mjs`.
- Commit straight to **`main`** (aegis convention). TDD: red → green → commit.
- **Path safety** everywhere: resolve under `files_root` and reject traversal,
  mirroring `SubscriptionRegistry.file_read`.
- Both HTTP routes require the WS token as `?t=<web_cfg.token>` (in addition to
  Caddy basic-auth in prod).
- `MAX_UPLOAD_BYTES = 50 * 1024 * 1024`.
- Uploads land under `files_root/.aegis/uploads/<handle>/` (agent-reachable +
  serveable by `/download`).
- **Parity exception is intentional** — see the spec's "Feature-parity note".
  `aegis_offer_file` degrades to the TUI viewer via `bridge.open_file`.

## File Structure

- `src/aegis/web/uploads.py` — **create**: pure helpers `safe_filename`,
  `upload_dir`, `save_upload`.
- `src/aegis/web/server.py` — **modify**: `GET /download`, `POST /upload` routes.
- `src/aegis/mcp/server.py` — **modify**: `aegis_offer_file` tool + briefing line.
- `src/aegis/web/compact.py` — **modify**: exempt `aegis_offer_file` from
  `ToolUse` `raw_input` stripping so the path survives compaction.
- `src/aegis/web/static/js/renderEvent.js` — **modify**: download-bubble for the
  `aegis_offer_file` tool event.
- `src/aegis/web/static/js/app.js` — **modify**: `.download-offer` click →
  navigate to `/download`; composer 📎 + attachment chips + weave-on-send;
  viewer Download button.
- `src/aegis/web/static/js/attach.js` — **create**: pure `weaveAttachments`.
- `src/aegis/web/static/css/base.css` — **modify**: attach + download styles.
- `pyproject.toml` — **modify**: add explicit `python-multipart` dep.
- Tests: `tests/test_web_download.py`, `tests/test_web_upload.py`,
  `tests/test_web_offer_file.py`, `tests/test_web_uploads_helper.py` (create);
  `tests/web/renderEvent.test.mjs`, `tests/web/attach.test.mjs` (create/modify);
  `tests/test_web_compact.py` (modify).

---

## Task 1: `GET /download` route (path-safe, tokened, attachment)

**Files:**
- Modify: `src/aegis/web/server.py`
- Test: `tests/test_web_download.py` (create)

**Interfaces:**
- Produces: `GET /download?path=<rel>&t=<token>` → the file with
  `Content-Disposition: attachment`; 401 bad token, 403 traversal, 404 missing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_download.py`:

```python
from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from aegis.config import WebConfig
from aegis.web.server import build_web_app


class FakeManager:
    def list_agents(self): return []
    def list_sessions(self): return []
    def get(self, h): return None


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "hello.txt").write_text("hi there", encoding="utf-8")
    app = build_web_app(FakeManager(), WebConfig(token="secret"),
                        tmp_path / "state", files_root=tmp_path,
                        server_version="9")
    return TestClient(app)


def test_download_serves_in_tree_file(tmp_path):
    r = _client(tmp_path).get("/download?path=hello.txt&t=secret")
    assert r.status_code == 200
    assert r.text == "hi there"
    assert "attachment" in r.headers["content-disposition"]
    assert "hello.txt" in r.headers["content-disposition"]


def test_download_bad_token_401(tmp_path):
    r = _client(tmp_path).get("/download?path=hello.txt&t=WRONG")
    assert r.status_code == 401


def test_download_traversal_403(tmp_path):
    r = _client(tmp_path).get("/download?path=../secret&t=secret")
    assert r.status_code == 403


def test_download_missing_404(tmp_path):
    r = _client(tmp_path).get("/download?path=nope.txt&t=secret")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_web_download.py -q`
Expected: FAIL — `/download` 404 (route absent).

- [ ] **Step 3: Add the route**

In `src/aegis/web/server.py`, add `FileResponse` to the responses import:

```python
from starlette.responses import (
    FileResponse, HTMLResponse, JSONResponse, Response)
```

Inside `build_web_app`, near the other handlers, add (uses the `files_root` and
`web_cfg` closure vars):

```python
    async def download(request):
        if request.query_params.get("t") != web_cfg.token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if files_root is None:
            return JSONResponse({"error": "files unavailable"}, status_code=404)
        root = Path(files_root).resolve()
        target = (root / request.query_params.get("path", "")).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return JSONResponse({"error": "path outside project"}, status_code=403)
        if not target.is_file():
            return JSONResponse({"error": "not a file"}, status_code=404)
        return FileResponse(target, filename=target.name)
```

Add `Route("/download", download),` to the `routes` list.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_web_download.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/server.py tests/test_web_download.py
git commit -m "feat(web): GET /download — path-safe, tokened, attachment"
```

---

## Task 2: `aegis_offer_file` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_web_offer_file.py` (create)

**Interfaces:**
- Produces: MCP tool `aegis_offer_file(path: str, label: str | None = None) ->
  {status: "offered"|"no_file", name, path, label}`. `path` is resolved
  relative to `Path.cwd()` (the project root) and returned project-relative.
  Also opens in the TUI viewer via `bridge.open_file` when available.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_offer_file.py` (mirror `tests/test_mcp_config_tools.py`'s
`build_server` + `_call` harness — copy its `_StubBridge` and `_call` helper):

```python
from __future__ import annotations

import json

import pytest

from aegis.mcp.server import build_server


class _StubBridge:
    async def open_file(self, path): return {"status": "ok"}


async def _call(server, name, **kwargs):
    tool = await server.get_tool(name)
    res = await tool.run(kwargs)
    return json.loads(res.content[0].text)


@pytest.mark.asyncio
async def test_offer_file_valid(tmp_path, monkeypatch):
    (tmp_path / "out.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_offer_file", path="out.pdf")
    assert data["status"] == "offered"
    assert data["name"] == "out.pdf"
    assert data["path"] == "out.pdf"


@pytest.mark.asyncio
async def test_offer_file_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_offer_file", path="nope.pdf")
    assert data["status"] == "no_file"
```

Note: adapt `_call` to the real harness in `test_mcp_config_tools.py` (read it
first — the exact `get_tool`/`run` shape and how results are unwrapped may
differ; reuse whatever that file already does).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_web_offer_file.py -q`
Expected: FAIL — unknown tool `aegis_offer_file`.

- [ ] **Step 3: Add the tool**

In `src/aegis/mcp/server.py`, ensure `import contextlib` and `from pathlib
import Path` are available at the point of use (import locally if needed). Add,
next to `aegis_view_file` inside `build_server`:

```python
    async def aegis_offer_file(path: str, label: str | None = None) -> dict:
        """Offer a file to the connected UI. On the web client it renders a
        download bubble in the transcript; on the TUI it opens the file in a
        viewer tab; headless it just validates. `path` is absolute or relative
        to aegis's cwd (the project root)."""
        import contextlib
        from pathlib import Path
        root = Path.cwd().resolve()
        p = Path(path)
        target = (p if p.is_absolute() else root / p).resolve()
        if not target.is_file():
            return {"status": "no_file", "path": path}
        try:
            rel = str(target.relative_to(root))
        except ValueError:
            rel = str(target)
        open_file = getattr(bridge, "open_file", None)
        if open_file is not None:
            with contextlib.suppress(Exception):
                await open_file(str(target))
        return {"status": "offered", "name": target.name, "path": rel,
                "label": label or target.name}

    server.tool(aegis_offer_file)
```

Add a briefing line near the `aegis_view_file` entry in `BRIEFING` (search for
existing tool descriptions):

```python
    "  - aegis_offer_file(path, label?) : hand a file to the connected UI — "
    "a download bubble on the web client, a viewer tab on the TUI.\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_web_offer_file.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_web_offer_file.py
git commit -m "feat(mcp): aegis_offer_file — offer a file to the connected UI"
```

---

## Task 3: Exempt `aegis_offer_file` from `raw_input` compaction

The compact wire strips `ToolUse.raw_input`; the download bubble needs the path,
so keep `raw_input` for this one tool.

**Files:**
- Modify: `src/aegis/web/compact.py`
- Test: `tests/test_web_compact.py`

**Interfaces:**
- Consumes: `compact_encoded` (existing).
- Produces: a `ToolUse` dict whose `name` ends with `aegis_offer_file` keeps its
  `raw_input`; other tools still drop it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_compact.py`:

```python
def test_offer_file_keeps_raw_input():
    from aegis.state.event_codec import encode_event
    from aegis.events import ToolUse
    d = encode_event(ToolUse(name="mcp__aegis__aegis_offer_file",
                             summary="offer", raw_input={"path": "out.pdf"}))
    out, truncated = compact_encoded(d)
    assert out.get("raw_input") == {"path": "out.pdf"}
    assert truncated is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_web_compact.py::test_offer_file_keeps_raw_input -q`
Expected: FAIL — `raw_input` was dropped.

- [ ] **Step 3: Add the exemption**

In `src/aegis/web/compact.py`, in the `ToolUse` branch, guard the drop:

```python
    if t == "ToolUse":
        name = d.get("name") or ""
        if d.get("raw_input") is None or name.endswith("aegis_offer_file"):
            return d, False
        out = dict(d)
        out.pop("raw_input", None)
        return out, True
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_web_compact.py -q`
Expected: PASS (all, including the existing `test_tool_use_drops_raw_input`).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/compact.py tests/test_web_compact.py
git commit -m "feat(web): keep raw_input for aegis_offer_file through compaction"
```

---

## Task 4: Download-bubble renderer + click-to-download

**Files:**
- Modify: `src/aegis/web/static/js/renderEvent.js`
- Modify: `src/aegis/web/static/js/app.js`
- Modify: `src/aegis/web/static/css/base.css`
- Test: `tests/web/renderEvent.test.mjs`

**Interfaces:**
- Consumes: a `ToolUse` rec whose `event.name` ends with `aegis_offer_file` and
  `event.raw_input.path` is the project-relative path.
- Produces: `<a class="download-offer" data-path="…">⤓ <label></a>`; app.js
  navigates `.download-offer` clicks to `/download?path=…&t=<token>`.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/renderEvent.test.mjs`:

```js
// aegis_offer_file tool event → download bubble
{
  const html = renderEvent(rec("ToolUse",
    { t: "ToolUse", name: "mcp__aegis__aegis_offer_file", summary: "offer",
      raw_input: { path: "out/report.pdf", label: "report.pdf" } }));
  assert.ok(html.includes("download-offer"));
  assert.ok(html.includes('data-path="out/report.pdf"'));
  assert.ok(html.includes("report.pdf"));
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/web/renderEvent.test.mjs`
Expected: FAIL — no `download-offer` (renders a normal tool chip).

- [ ] **Step 3: Add the renderer branch**

In `src/aegis/web/static/js/renderEvent.js`, at the top of the `ToolUse` branch
(before the normal tool-use render):

```js
  if (t === "ToolUse" && (ev.name || "").endsWith("aegis_offer_file")) {
    const ri = ev.raw_input || {};
    const path = ri.path || "";
    const label = ri.label || (path.split("/").pop() || "file");
    return `<div class="tool-use"><a class="download-offer" `
      + `data-path="${esc(path)}">⤓ ${esc(label)}</a></div>`;
  }
```

- [ ] **Step 4: Run to verify it passes**

Run: `node tests/web/renderEvent.test.mjs`
Expected: prints OK.

- [ ] **Step 5: Wire the click in `app.js`**

In the delegated `panesEl.addEventListener("click", …)` handler (the one that
already handles `.expand`), add at the top of the callback:

```js
  const dl = e.target.closest(".download-offer");
  if (dl) {
    const path = dl.dataset.path;
    const url = "/download?path=" + encodeURIComponent(path)
      + "&t=" + encodeURIComponent(token);
    window.location.assign(url);   // triggers the attachment download
    return;
  }
```

(`token` is the module-scope token in app.js.)

- [ ] **Step 6: Add styles**

Append to `src/aegis/web/static/css/base.css`:

```css
.download-offer { cursor: pointer; text-decoration: none;
                  display: inline-block; padding: 0.35rem 0.7rem;
                  border-radius: 6px; background: var(--aegis-accent, #e6b450);
                  color: #111; font-weight: 600; }
.download-offer:hover { filter: brightness(1.05); }
```

- [ ] **Step 7: Commit**

```bash
git add src/aegis/web/static/js/renderEvent.js src/aegis/web/static/js/app.js src/aegis/web/static/css/base.css tests/web/renderEvent.test.mjs
git commit -m "feat(web): render aegis_offer_file as a click-to-download bubble"
```

---

## Task 5: Upload helpers (`web/uploads.py`)

**Files:**
- Create: `src/aegis/web/uploads.py`
- Test: `tests/test_web_uploads_helper.py` (create)

**Interfaces:**
- Produces: `safe_filename(name) -> str` (basename, `[A-Za-z0-9._-]` only,
  ≤200 chars, never empty); `upload_dir(files_root, handle) -> Path`
  (`files_root/.aegis/uploads/<safe-handle>/`); `save_upload(files_root, handle,
  filename, data: bytes) -> str` (writes, returns the `files_root`-relative path).

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_uploads_helper.py`:

```python
from pathlib import Path

from aegis.web.uploads import safe_filename, save_upload, upload_dir


def test_safe_filename_strips_path_and_unsafe():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("a b/c!.txt") == "c_.txt"
    assert safe_filename("") == "file"


def test_upload_dir_is_under_root(tmp_path):
    d = upload_dir(tmp_path, "swift-bohr")
    assert d == tmp_path / ".aegis" / "uploads" / "swift-bohr"


def test_save_upload_writes_and_returns_relpath(tmp_path):
    rel = save_upload(tmp_path, "h", "r.csv", b"a,b\n1,2\n")
    assert rel == ".aegis/uploads/h/r.csv"
    assert (tmp_path / rel).read_bytes() == b"a,b\n1,2\n"


def test_save_upload_sanitizes_traversal(tmp_path):
    rel = save_upload(tmp_path, "h", "../evil.sh", b"x")
    assert rel == ".aegis/uploads/h/evil.sh"
    assert (tmp_path / rel).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_web_uploads_helper.py -q`
Expected: FAIL — no module `aegis.web.uploads`.

- [ ] **Step 3: Implement `uploads.py`**

Create `src/aegis/web/uploads.py`:

```python
"""Path-safe helpers for browser file uploads. Files land under
``files_root/.aegis/uploads/<handle>/`` so they are both agent-reachable and
serveable by the path-safe ``/download`` route."""
from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(name: str) -> str:
    base = _UNSAFE.sub("_", Path(name).name).lstrip(".")
    return (base or "file")[:200]


def upload_dir(files_root, handle: str) -> Path:
    safe_handle = _UNSAFE.sub("_", handle) or "session"
    return Path(files_root) / ".aegis" / "uploads" / safe_handle


def save_upload(files_root, handle: str, filename: str, data: bytes) -> str:
    root = Path(files_root).resolve()
    d = upload_dir(root, handle)
    d.mkdir(parents=True, exist_ok=True)
    target = d / safe_filename(filename)
    target.write_bytes(data)
    return str(target.relative_to(root))
```

Note: `safe_filename` strips leading dots so `../evil.sh` → `evil.sh` (not
`..evil.sh`); verify the `test_save_upload_sanitizes_traversal` expectation
matches — `Path("../evil.sh").name` is `evil.sh`, so it already collapses.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/test_web_uploads_helper.py -q`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/uploads.py tests/test_web_uploads_helper.py
git commit -m "feat(web): uploads.py — path-safe scratch-dir save helpers"
```

---

## Task 6: `POST /upload` route

**Files:**
- Modify: `src/aegis/web/server.py`
- Modify: `pyproject.toml` (explicit `python-multipart`)
- Test: `tests/test_web_upload.py` (create)

**Interfaces:**
- Consumes: `save_upload` (Task 5), `web_cfg.token`, `files_root`.
- Produces: `POST /upload?handle=<h>&t=<token>` (multipart) →
  `{files: [{name, path, size}]}`; 401 bad token, 413 oversize.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_upload.py`:

```python
from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from aegis.config import WebConfig
from aegis.web.server import build_web_app


class FakeManager:
    def list_agents(self): return []
    def list_sessions(self): return []
    def get(self, h): return None


def _client(tmp_path: Path) -> TestClient:
    app = build_web_app(FakeManager(), WebConfig(token="secret"),
                        tmp_path / "state", files_root=tmp_path,
                        server_version="9")
    return TestClient(app)


def test_upload_saves_and_returns_path(tmp_path):
    c = _client(tmp_path)
    r = c.post("/upload?handle=swift-bohr&t=secret",
               files={"file": ("r.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 200
    files = r.json()["files"]
    assert files[0]["name"] == "r.csv"
    assert files[0]["path"] == ".aegis/uploads/swift-bohr/r.csv"
    assert (tmp_path / files[0]["path"]).read_bytes() == b"a,b\n1,2\n"


def test_upload_bad_token_401(tmp_path):
    r = _client(tmp_path).post("/upload?handle=h&t=WRONG",
                               files={"file": ("x.txt", b"x", "text/plain")})
    assert r.status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_web_upload.py -q`
Expected: FAIL — `/upload` 404/405.

- [ ] **Step 3: Add `python-multipart` to deps**

In `pyproject.toml`, add to the dependencies list (Starlette needs it for form
parsing): `"python-multipart>=0.0.20",`. Then `uv sync` (or `uv lock`) so it's
pinned. (It's already installed transitively; this makes it explicit.)

- [ ] **Step 4: Add the route**

In `src/aegis/web/server.py`, import the helper and constant near the top:

```python
from aegis.web.uploads import save_upload

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
```

Add the handler inside `build_web_app`:

```python
    async def upload(request):
        if request.query_params.get("t") != web_cfg.token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if files_root is None:
            return JSONResponse({"error": "files unavailable"}, status_code=404)
        clen = int(request.headers.get("content-length") or 0)
        if clen > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "file too large"}, status_code=413)
        handle = request.query_params.get("handle") or "session"
        form = await request.form()
        saved = []
        for _key, val in form.multi_items():
            filename = getattr(val, "filename", None)
            if not filename:
                continue
            data = await val.read()
            if len(data) > MAX_UPLOAD_BYTES:
                return JSONResponse({"error": "file too large"}, status_code=413)
            rel = save_upload(files_root, handle, filename, data)
            saved.append({"name": filename, "path": rel, "size": len(data)})
        return JSONResponse({"files": saved})
```

Add `Route("/upload", upload, methods=["POST"]),` to the `routes` list.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run python -m pytest tests/test_web_upload.py -q`
Expected: PASS (2).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/web/server.py pyproject.toml uv.lock tests/test_web_upload.py
git commit -m "feat(web): POST /upload — multipart to per-session scratch dir"
```

---

## Task 7: Composer attach + weave-on-send

**Files:**
- Create: `src/aegis/web/static/js/attach.js`
- Modify: `src/aegis/web/static/js/app.js`
- Modify: `src/aegis/web/static/index.html`
- Modify: `src/aegis/web/static/css/base.css`
- Test: `tests/web/attach.test.mjs` (create)

**Interfaces:**
- Produces: `weaveAttachments(text, attachments) -> string` where
  `attachments` is `[{name, path}]` — prepends an `[attached files: …]` block,
  or returns `text` unchanged when empty.

- [ ] **Step 1: Write the failing test**

Create `tests/web/attach.test.mjs`:

```js
// Run: node tests/web/attach.test.mjs
import assert from "node:assert";
import { weaveAttachments } from "../../src/aegis/web/static/js/attach.js";

assert.equal(weaveAttachments("hi", []), "hi");
{
  const out = weaveAttachments("summarize this",
    [{ name: "r.csv", path: ".aegis/uploads/h/r.csv" }]);
  assert.ok(out.includes("r.csv → .aegis/uploads/h/r.csv"));
  assert.ok(out.includes("summarize this"));
  assert.ok(out.startsWith("[attached files:"));
}
// attachments but no text → just the block
assert.ok(weaveAttachments("", [{ name: "a", path: "p" }]).includes("a → p"));
console.log("attach.test.mjs OK");
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/web/attach.test.mjs`
Expected: FAIL — no module.

- [ ] **Step 3: Implement `attach.js`**

Create `src/aegis/web/static/js/attach.js`:

```js
// Weave uploaded-file paths into the message text delivered to the agent, so a
// chat attachment reaches the agent as a readable path. No protocol change —
// the agent (full permission on the workspace) reads the path with its tools.
export function weaveAttachments(text, attachments) {
  if (!attachments || !attachments.length) return text;
  const lines = attachments.map((a) => `- ${a.name} → ${a.path}`).join("\n");
  const block = `[attached files:\n${lines}]`;
  return text ? `${block}\n\n${text}` : block;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `node tests/web/attach.test.mjs`
Expected: prints OK.

- [ ] **Step 5: Add the composer UI**

In `src/aegis/web/static/index.html`, inside `<footer id="composer">` before the
`<textarea>`:

```html
      <div id="attach-chips"></div>
      <button id="attach-btn" type="button" title="Attach a file">📎</button>
      <input id="attach-input" type="file" multiple hidden />
```

- [ ] **Step 6: Wire upload + weave in `app.js`**

Import the helper (top of app.js):

```js
import { weaveAttachments } from "./attach.js";
```

Add attachment state + wiring (near `wireComposer`):

```js
let pendingAttachments = [];   // [{name, path, size}]
const attachInput = document.getElementById("attach-input");
const attachBtn = document.getElementById("attach-btn");
const attachChips = document.getElementById("attach-chips");

function renderAttachChips() {
  attachChips.innerHTML = "";
  pendingAttachments.forEach((a, i) => {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    chip.textContent = a.name + " ×";
    chip.addEventListener("click", () => {
      pendingAttachments.splice(i, 1); renderAttachChips();
    });
    attachChips.appendChild(chip);
  });
}

if (attachBtn) attachBtn.addEventListener("click", () => attachInput.click());
if (attachInput) attachInput.addEventListener("change", async () => {
  if (!activeHandle || !attachInput.files.length) return;
  const fd = new FormData();
  for (const f of attachInput.files) fd.append("file", f);
  attachInput.value = "";
  try {
    const url = "/upload?handle=" + encodeURIComponent(activeHandle)
      + "&t=" + encodeURIComponent(token);
    const res = await fetch(url, { method: "POST", body: fd });
    const data = await res.json();
    if (data.files) { pendingAttachments.push(...data.files); renderAttachChips(); }
    else showError("upload failed");
  } catch (e) { showError("upload failed: " + e.message); }
});
```

Then update the composer send (in `wireComposer`'s keydown handler) to weave and
clear attachments. Replace the send block:

```js
      const text = input.value.trim();
      if ((text || pendingAttachments.length) && activeHandle) {
        const message = weaveAttachments(text, pendingAttachments);
        client.rpc("deliver", { handle: activeHandle, message })
          .catch((err) => showError("deliver failed: " + err.message));
        input.value = "";
        pendingAttachments = [];
        renderAttachChips();
        autogrow();
      }
```

- [ ] **Step 7: Styles**

Append to `src/aegis/web/static/css/base.css`:

```css
#attach-btn { background: none; border: 0; cursor: pointer; font-size: 1.1rem; }
#attach-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.attach-chip { cursor: pointer; font-size: 0.8em; padding: 0.15rem 0.5rem;
               border-radius: 999px; background: var(--aegis-muted, #444);
               color: #eee; }
```

- [ ] **Step 8: Verify + browser smoke**

Run: `node tests/web/attach.test.mjs && node --check src/aegis/web/static/js/app.js`
Then `aegis web`: attach a file, confirm a chip appears; send with an
instruction; confirm the agent receives the woven path and can read the file.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/web/static/js/attach.js src/aegis/web/static/js/app.js src/aegis/web/static/index.html src/aegis/web/static/css/base.css tests/web/attach.test.mjs
git commit -m "feat(web): composer file attach — upload + weave paths into send"
```

---

## Task 8: Download button on the file viewer (pull path)

**Files:**
- Modify: `src/aegis/web/static/js/app.js` (`openFileViewer`)

**Interfaces:**
- Consumes: `GET /download` (Task 1), the viewer's current `path`.

- [ ] **Step 1: Add a Download control in `openFileViewer`**

In `src/aegis/web/static/js/app.js::openFileViewer(path)`, after the viewer modal
is built, add a Download button that navigates to the download route:

```js
  const dlBtn = document.createElement("button");
  dlBtn.textContent = "⤓ Download";
  dlBtn.className = "file-download";
  dlBtn.addEventListener("click", () => {
    window.location.assign("/download?path=" + encodeURIComponent(path)
      + "&t=" + encodeURIComponent(token));
  });
```

Mount `dlBtn` into the viewer's header/toolbar element (match how the viewer
modal is assembled — read the surrounding `openFileViewer` code and append the
button to its header container).

- [ ] **Step 2: Browser smoke**

`aegis web` → Alt+P → open a file → click ⤓ Download → the file downloads.

- [ ] **Step 3: Commit**

```bash
git add src/aegis/web/static/js/app.js
git commit -m "feat(web): Download button on the file viewer"
```

---

## Task 9: Full gate + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: JS tests**

Run: `for f in tests/web/*.test.mjs; do node "$f" || exit 1; done; echo ALL_JS_OK`
Expected: `ALL_JS_OK`.

- [ ] **Step 2: Python web + mcp + compact suites**

Run: `uv run python -m pytest tests/test_web_*.py tests/test_mcp_config_tools.py -q; echo "EXIT=$?"`
Expected: `EXIT=0`.

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]`:

```markdown
- Web file transfer: attach files in the composer (`POST /upload` → per-session
  scratch dir, paths woven into your message) and receive files from the agent
  as click-to-download bubbles via the new `aegis_offer_file` MCP tool
  (`GET /download`, path-safe + tokened). A Download button on the file viewer
  grabs any workspace file. (Web-only by design — file transfer is a
  remote-access capability; `aegis_offer_file` degrades to the TUI viewer.)
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): web file transfer (upload + download)"
git push origin main
```

Then redeploy per `know-how/deploying-web.md` (VPS pull + `systemctl restart
aegis-web`) and browser-smoke the round trip.

---

## Self-Review notes (for the executor)

- **Spec coverage:** F1 = Tasks 1–4 (download route, tool, compaction exemption,
  renderer); F2 = Tasks 5–7 (helpers, upload route, composer); F3 = Task 8
  (viewer button). Security/limits are in Tasks 1/5/6; parity note is honored by
  Task 2's `bridge.open_file` degradation.
- **Compaction exemption (Task 3) is load-order-independent** of the renderer
  (Task 4) but both are needed for the bubble to work end-to-end — F1 isn't
  demoable until Task 4.
- **Token in the URL:** `/download` and `/upload` take `?t=`; the browser also
  carries Caddy basic-auth in prod. Don't log these URLs.
- **`_call`/`get_tool` harness (Task 2):** read `tests/test_mcp_config_tools.py`
  and reuse its exact tool-invocation helper — FastMCP's result-unwrapping shape
  is what that file already encodes; don't invent a new one.
- **MCP tool name:** the agent-visible name is `mcp__aegis__aegis_offer_file`
  (server injected as `mcpServers.aegis`), so the renderer matches
  `endsWith("aegis_offer_file")`.
