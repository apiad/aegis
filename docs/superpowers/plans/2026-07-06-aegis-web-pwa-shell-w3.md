# Aegis Web PWA Shell (W3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web client an installable PWA — an app-shell that launches
instantly (cache-first service worker), works installed, survives a flaky link,
and shows a clear reconnecting state — without an offline outbox.

**Architecture:** A `manifest.webmanifest` + SVG icon make it installable
(`start_url: "/"`, reusing the token already persisted in `localStorage`). A
`service-worker.js` served at root scope precaches the shell and serves it
cache-first, with a cache name keyed to `server_version` so each deploy busts
it. `ws.js` emits a synthetic `connection` event that drives a reconnecting
banner. Live WS traffic is never cached; it degrades to the banner and catches
up via the existing `resume` flow.

**Tech Stack:** Vanilla ES modules, service worker API, Starlette, `uv`.

## Global Constraints

- Python **3.13+**, `uv`. Python tests: `uv run python -m pytest -q -m "not live"`
  (`python -m pytest`, not bare `uv run pytest`). Never `-k "not live"`. Never
  pipe pytest into `tail` — check the real exit code.
- JS tests: dependency-free node scripts, `node tests/web/<name>.test.mjs`.
- No build step, no framework, **no new Python/JS dependencies** (icon is SVG —
  no image tooling).
- Commit straight to **`main`** (aegis convention). TDD where a unit test fits;
  the service worker + manifest wiring is verified by server tests + a browser
  smoke (SW behavior can't run under node).
- Depends on W0–W2 (merged): the client already renders from the compact wire
  and persists the token in `localStorage` (`app.js` "token" block).

## File Structure

- `src/aegis/web/static/manifest.webmanifest` — **create**: PWA manifest.
- `src/aegis/web/static/icons/icon.svg` — **create**: app icon.
- `src/aegis/web/static/service-worker.js` — **create**: precache + cache-first
  shell; `__SW_VERSION__` placeholder templated at serve time.
- `src/aegis/web/static/index.html` — **modify**: manifest/theme-color/icon
  links + SW registration + reconnecting banner element.
- `src/aegis/web/server.py` — **modify**: routes for `/manifest.webmanifest` and
  `/service-worker.js` (root scope, version substitution).
- `src/aegis/web/static/js/ws.js` — **modify**: emit `connection` on hello/close.
- `src/aegis/web/static/js/app.js` — **modify**: `connection` handler → banner.
- `src/aegis/web/static/css/base.css` — **modify**: banner styles.
- Tests: `tests/test_web_pwa.py` (**create**), `tests/web/ws_connection.test.mjs`
  (**create**).

---

## Task 1: Manifest + icon + head links

**Files:**
- Create: `src/aegis/web/static/manifest.webmanifest`
- Create: `src/aegis/web/static/icons/icon.svg`
- Modify: `src/aegis/web/static/index.html`
- Modify: `src/aegis/web/server.py`
- Test: `tests/test_web_pwa.py` (create)

**Interfaces:**
- Produces: `GET /manifest.webmanifest` → the manifest (`application/manifest+json`);
  `GET /static/icons/icon.svg` → the icon; `index.html` links both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_pwa.py` (mirror `tests/test_web_server.py`'s
`build_web_app` + `TestClient` setup — copy its `FakeManager` + `_app(tmp_path)`
helper or import the pattern):

```python
from __future__ import annotations

import json
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
                        server_version="9.9.9")
    return TestClient(app)


def test_manifest_served(tmp_path):
    r = _client(tmp_path).get("/manifest.webmanifest")
    assert r.status_code == 200
    data = json.loads(r.text)
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"
    assert data["icons"]


def test_index_links_manifest_and_registers_sw(tmp_path):
    html = _client(tmp_path).get("/").text
    assert 'rel="manifest"' in html
    assert "serviceWorker" in html
    assert 'name="theme-color"' in html


def test_icon_served(tmp_path):
    r = _client(tmp_path).get("/static/icons/icon.svg")
    assert r.status_code == 200
    assert "svg" in r.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_web_pwa.py -q`
Expected: FAIL — `/manifest.webmanifest` 404 / no manifest link in index.

- [ ] **Step 3: Create the manifest**

Create `src/aegis/web/static/manifest.webmanifest`:

```json
{
  "name": "aegis",
  "short_name": "aegis",
  "description": "aegis — multi-agent coding harness",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#0b0e14",
  "theme_color": "#0b0e14",
  "icons": [
    {
      "src": "/static/icons/icon.svg",
      "sizes": "any",
      "type": "image/svg+xml",
      "purpose": "any maskable"
    }
  ]
}
```

- [ ] **Step 4: Create the icon**

Create `src/aegis/web/static/icons/icon.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#0b0e14"/>
  <text x="50%" y="56%" text-anchor="middle" dominant-baseline="middle"
        font-family="ui-monospace, monospace" font-size="280"
        fill="#e6b450">æ</text>
</svg>
```

- [ ] **Step 5: Add head links to `index.html`**

In `src/aegis/web/static/index.html`, inside `<head>` after the `<title>`:

```html
  <meta name="theme-color" content="#0b0e14" />
  <link rel="manifest" href="/manifest.webmanifest" />
  <link rel="icon" href="/static/icons/icon.svg" />
  <link rel="apple-touch-icon" href="/static/icons/icon.svg" />
```

And just before `</body>` (after the app.js module script), the SW registration:

```html
  <script>
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js").catch(() => {});
      });
    }
  </script>
```

- [ ] **Step 6: Serve the manifest at root path in `server.py`**

In `build_web_app`, add a route. After the existing `index_html`/`base_css`
reads, add a manifest reader and handler:

```python
    manifest_json = (static / "manifest.webmanifest").read_text(encoding="utf-8")

    async def manifest(request):
        return Response(manifest_json, media_type="application/manifest+json")
```

Add `Route("/manifest.webmanifest", manifest),` to the `routes` list.
(The icon is served by the existing `Mount("/static", StaticFiles(...))`.)

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_web_pwa.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/web/static/manifest.webmanifest src/aegis/web/static/icons/icon.svg src/aegis/web/static/index.html src/aegis/web/server.py tests/test_web_pwa.py
git commit -m "feat(web): PWA manifest + icon + install head links"
```

---

## Task 2: Service worker — precache shell, cache-first, version-busted

**Files:**
- Create: `src/aegis/web/static/service-worker.js`
- Modify: `src/aegis/web/server.py` (root-scope route + version substitution)
- Test: `tests/test_web_pwa.py`

**Interfaces:**
- Produces: `GET /service-worker.js` → the SW JS with `Service-Worker-Allowed: /`
  and `__SW_VERSION__` replaced by `server_version`.

- [ ] **Step 1: Add the failing server test**

Append to `tests/test_web_pwa.py`:

```python
def test_service_worker_served_root_scope_versioned(tmp_path):
    r = _client(tmp_path).get("/service-worker.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert "9.9.9" in r.text                 # version substituted
    assert "__SW_VERSION__" not in r.text
    assert "/static/js/app.js" in r.text     # precaches the shell
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_web_pwa.py::test_service_worker_served_root_scope_versioned -q`
Expected: FAIL — 404.

- [ ] **Step 3: Create the service worker**

Create `src/aegis/web/static/service-worker.js`:

```js
// aegis PWA service worker: precache the app shell, serve it cache-first so
// the app launches instantly and works installed offline. Live WS traffic is
// never cached (it isn't a fetch event). The cache name carries the server
// version — a new deploy changes these bytes, so the SW reinstalls and busts
// the old cache.
const VERSION = "__SW_VERSION__";
const CACHE = `aegis-shell-${VERSION}`;
const SHELL = [
  "/",
  "/static/js/app.js",
  "/static/js/ws.js",
  "/static/js/coalesce.js",
  "/static/js/markdown.js",
  "/static/js/renderEvent.js",
  "/static/js/tabs.js",
  "/static/js/queues.js",
  "/theme.css",
  "/manifest.webmanifest",
  "/static/icons/icon.svg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (req.mode === "navigate") {
    // Network-first for navigations; fall back to the cached shell offline.
    e.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }
  // Cache-first for static assets.
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
```

- [ ] **Step 4: Serve it at root scope with version substitution**

In `src/aegis/web/server.py`, add alongside the manifest handler:

```python
    sw_src = (static / "service-worker.js").read_text(encoding="utf-8")

    async def service_worker(request):
        body = sw_src.replace("__SW_VERSION__", server_version)
        return Response(body, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/",
                                 "Cache-Control": "no-cache"})
```

Add `Route("/service-worker.js", service_worker),` to `routes`.

- [ ] **Step 5: Run tests green**

Run: `uv run python -m pytest tests/test_web_pwa.py -q`
Expected: PASS (all PWA tests).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/web/static/service-worker.js src/aegis/web/server.py tests/test_web_pwa.py
git commit -m "feat(web): service worker — precache shell, cache-first, version-busted"
```

---

## Task 3: Reconnecting banner (flaky-link UX)

**Files:**
- Modify: `src/aegis/web/static/js/ws.js`
- Modify: `src/aegis/web/static/js/app.js`
- Modify: `src/aegis/web/static/index.html`
- Modify: `src/aegis/web/static/css/base.css`
- Test: `tests/web/ws_connection.test.mjs` (create)

**Interfaces:**
- Produces: `WSClient` dispatches a `connection` stream-kind `{connected: bool}`
  to `on("connection", fn)` — `true` on `hello`, `false` on socket close.

- [ ] **Step 1: Write the failing node test**

Create `tests/web/ws_connection.test.mjs`:

```js
// Run: node tests/web/ws_connection.test.mjs
import assert from "node:assert";
import { WSClient } from "../../src/aegis/web/static/js/ws.js";

const seen = [];
const c = new WSClient("ws://x", "tok");
c.on("connection", (f) => seen.push(f.connected));

// hello → connected true
c._handle({ type: "hello", constants: {} });
assert.deepEqual(seen, [true]);

// explicit disconnect signal → connected false
c._emitConnection(false);
assert.deepEqual(seen, [true, false]);
console.log("ws_connection.test.mjs OK");
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/web/ws_connection.test.mjs`
Expected: FAIL — no `connection` dispatch / `_emitConnection` undefined.

- [ ] **Step 3: Emit `connection` from `ws.js`**

In `src/aegis/web/static/js/ws.js`, add a helper and call it. Add the method
(near `_dispatch`):

```js
  _emitConnection(connected) {
    this._dispatch("connection", { connected });
  }
```

In `_handle`, inside the `if (msg.type === "hello")` block, after
`this.connected = true;` add:

```js
      this._emitConnection(true);
```

In `_open`, inside `this.ws.onclose = () => {`, after `this.connected = false;`
add:

```js
      this._emitConnection(false);
```

- [ ] **Step 4: Run the node test green**

Run: `node tests/web/ws_connection.test.mjs`
Expected: `ws_connection.test.mjs OK`.

- [ ] **Step 5: Add the banner element to `index.html`**

In `src/aegis/web/static/index.html`, as the first child of `<div id="app">`:

```html
    <div id="conn-banner" hidden>reconnecting…</div>
```

- [ ] **Step 6: Wire the handler in `app.js`**

Add near the other frame handlers (e.g. after `onState`):

```js
const connBanner = document.getElementById("conn-banner");
function onConnection(frame) {
  if (connBanner) connBanner.hidden = frame.connected;
}
```

In `boot()`, register it with the other `client.on(...)` calls:

```js
  client.on("connection", onConnection);
```

- [ ] **Step 7: Style the banner in `base.css`**

Append to `src/aegis/web/static/css/base.css`:

```css
#conn-banner { position: sticky; top: 0; z-index: 20;
               text-align: center; padding: 0.25rem;
               font-size: 0.85em; background: #7a5; color: #111; }
#conn-banner[hidden] { display: none; }
```

- [ ] **Step 8: Commit**

```bash
git add src/aegis/web/static/js/ws.js src/aegis/web/static/js/app.js src/aegis/web/static/index.html src/aegis/web/static/css/base.css tests/web/ws_connection.test.mjs
git commit -m "feat(web): reconnecting banner driven by ws connection events"
```

---

## Task 4: Gate + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the JS tests**

Run: `for f in tests/web/*.test.mjs; do node "$f" || exit 1; done; echo ALL_JS_OK`
Expected: `ALL_JS_OK`.

- [ ] **Step 2: Run the web Python tests**

Run: `uv run python -m pytest tests/test_web_*.py -q; echo "EXIT=$?"`
Expected: `EXIT=0`.

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]`:

```markdown
- The web client is now an installable **PWA**: `manifest.webmanifest` + icon,
  a service worker that precaches the app shell and serves it cache-first
  (cache-busted per `server_version`), and a reconnecting banner for flaky
  links. Launches instantly and works installed; live actions require the
  connection (no offline outbox in v1).
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): installable PWA shell + reconnecting banner"
git push origin main
```

---

## Self-Review notes (for the executor)

- **Spec coverage (W3):** manifest+icon+install = Task 1; service worker
  (precache, cache-first, version-bust) = Task 2; reconnecting/offline UX =
  Task 3. Token persistence already exists (`app.js`), so `start_url:"/"` needs
  no new code — deliberately not a task.
- **Out of scope (per spec):** offline outbox (queue-and-send while
  disconnected). The composer is not disabled on disconnect in this plan; the
  banner is the signal. If desired, disabling the composer on `connection:false`
  is a one-line follow-up.
- **Icon is SVG only** (installable on Chromium, no image tooling). iOS
  home-screen polish (a raster `apple-touch-icon` PNG) is a follow-up; the SVG
  `apple-touch-icon` link is present but iOS may ignore it.
- **SW can't be node-tested** (needs the service worker runtime); it's covered
  by the server test (served, versioned, root scope) + the browser smoke. Verify
  in-browser: install prompt appears, offline reload serves the shell, killing
  the server shows the reconnecting banner then recovery.
- **`server_version`:** `WebFrontend` passes it into `build_web_app`; confirm
  the real version is threaded from `cli.py` (defaults to `"0"` — still valid,
  just a coarser cache key).
