# Aegis Web Client — S2b (Browser Client + CLI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Builds on S2a's WS server. The wire contract is `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`.

**Goal:** Turn S2a's headless WS server into a usable single-tab browser client — open a page, spawn an agent, watch events stream with TUI-fidelity rendering, type a message, interrupt with Esc — plus an `aegis web` command that launches it.

**Architecture:** No SPA, no build step. A static HTML shell + vanilla-JS modules served from `src/aegis/web/static/`. `ws.js` owns the socket (auth, rpc-as-promises, subscribe, reconnect+resume). `transcript.js`/`app.js` render events by mounting the **server-rendered `html`** from each `stream/event` frame, coalescing streaming text chunks client-side. Colors come entirely from CSS variables served at `/theme.css` (from S1's `to_css_variables()`), so the JS holds no theme state.

**Tech Stack:** Starlette routes (S2a app), vanilla ES modules, S1 theme CSS variables. Node (present) for one pure-logic unit test. No new Python deps; no JS framework.

## Global Constraints

- Build on S2a's `build_web_app`; the existing `/healthz` + `/ws` tests stay green.
- **No SPA / no build step / no bundler.** Plain ES modules loaded with `<script type="module">`.
- **The JS never renders event semantics itself** — it mounts the server's `html` string; only *streaming text coalescing* is client-side (the one pure function worth a unit test).
- Theme = `aegis-ink` for S2b (theme switching is S8). Colors via `var(--aegis-*)` from `/theme.css`.
- Commit straight to **main**. Conventional commits. TDD where a test layer exists (Python routes, node coalesce); careful authoring + live smoke for DOM behavior.

## File Structure

**New:**
- `src/aegis/web/static/index.html` — the shell (transcript area, input, status line).
- `src/aegis/web/static/css/base.css` — transcript + chrome styles using `var(--aegis-*)`.
- `src/aegis/web/static/js/coalesce.js` — pure: merge streaming `AssistantText`/`AssistantThinking` chunks by `(event_type, message_id)`. ES module, node-importable.
- `src/aegis/web/static/js/ws.js` — WS client: auth, `rpc(method, params) -> Promise`, `subscribe`, reconnect+resume, ping watchdog, frame dispatch.
- `src/aegis/web/static/js/app.js` — boot: read `?t=`, connect, spawn/pick session, render loop, input → `deliver`, Esc → `interrupt`, status line from `state` frames.
- `tests/test_web_static_routes.py` — Python route tests.
- `tests/web/coalesce.test.mjs` — node unit test for `coalesce.js`.

**Modified:**
- `src/aegis/web/server.py` — add `/` (index), `/theme.css` (S1 CSS vars), `/static` mount (default to package static dir).
- `src/aegis/cli.py` — add the `aegis web` command (launch co-resident serve + open browser).

---

### Task 1: Server routes — index, theme.css, static mount

**Files:** Modify `src/aegis/web/server.py`. Test: `tests/test_web_static_routes.py`.

**Interfaces — Produces (added to `build_web_app`):**
- `GET /` → `HTMLResponse` of `static/index.html`.
- `GET /theme.css` → `text/css` body = `load_theme("aegis-ink").to_css_variables()` followed by the contents of `static/css/base.css`. (One stylesheet: variables first, then rules that reference them.)
- `/static` mounted from the package `static/` dir (default when `static_dir` not overridden).

**Acceptance (TestClient):**
- `GET /` → 200, `text/html`, body contains `<div id="transcript"` and references `/theme.css` and `/static/js/app.js`.
- `GET /theme.css` → 200, `content-type` starts `text/css`, body contains `--aegis-bg: #0e0e0d` (S1 ink value) and at least one rule from base.css (e.g. `.tool-use`).
- `GET /static/js/app.js` → 200 (asset served).
- `/healthz` and `/ws` unchanged (re-run `test_web_server.py`).

TDD: write `tests/test_web_static_routes.py` → fail → create the static files (minimal stubs sufficient to pass: index.html with the required ids/links; base.css with a `.tool-use{}` rule) → wire routes → pass → commit. (Real CSS/JS content lands in later tasks; this task fixes the wiring + assets existence.)

---

### Task 2: `coalesce.js` — pure streaming-chunk merge (node-tested)

**Files:** Create `src/aegis/web/static/js/coalesce.js`, `tests/web/coalesce.test.mjs`.

**Interfaces — Produces:**
- `export function coalesceInto(history, frame)` — given the client's `history` array of rendered blocks and an incoming `stream/event` `frame`, either **append** a new block or **mutate** the in-flight streaming block. Returns `{action: "append"|"update", index}`. Streaming merge rule mirrors `aegis.render.coalesce_chunks`: consecutive `AssistantText` (or `AssistantThinking`) frames sharing the same `event.message_id` merge into one block (text concatenated); any other `event_type`, or a different `message_id`, starts a new block.
- A block record is `{seq, event_type, message_id, text, html}`.

**Acceptance (node, no deps):** `node tests/web/coalesce.test.mjs` exits 0. Cases: two AssistantText frames same message_id → one block, text concatenated, `action:"update"` on the second; different message_id → two blocks; a ToolUse frame between two text frames → three blocks; a frame with `html` and no message_id (e.g. ToolResult) → appended verbatim.

TDD: write the `.mjs` asserts (using `node:assert`) → run (fail, module missing) → implement `coalesce.js` → run (pass) → commit. The node test runs via `node` directly (present on PATH); it is not part of the pytest suite.

---

### Task 3: `ws.js` + `app.js` + real CSS/HTML — the client

**Files:** Flesh out `src/aegis/web/static/js/ws.js`, `js/app.js`, `index.html`, `css/base.css`.

**`ws.js` — Produces a `WSClient`:**
- `connect(token)` — open `WS /ws?t=<token>`, send `{type:"auth",token}`, await `hello`, store `constants`.
- `rpc(method, params)` — send `{type:"rpc", id, method, params}`, return a Promise resolved by the matching `rpc_response` (reject on `ok:false`/`error`).
- `subscribe(handle)` / `subscribeGlobal(stream)` — send subscribe frames; track per-handle `last_seq` from incoming `event`/`inbox` frames.
- `on(kind, fn)` — register a stream-frame handler (`event`/`state`/`inbox`/`history_complete`/`session_list`/`window_reset`).
- Reconnect loop with backoff; on reconnect re-auth then send `resume{subscriptions:[{handle,last_seq}], globals}`. Ping watchdog: ≥60s silence → force reconnect.

**`app.js` — boot + render:**
- Read `?t=` from URL → `localStorage`, strip from address bar (`history.replaceState`).
- Connect; if no sessions, `rpc("list_agents")` → pick first (S2b: simplest — spawn the default agent via `rpc("spawn_session",{agent_profile})` on a button/auto); subscribe to its handle.
- `on("event")` → `coalesceInto(history, frame)`; append/replace a `<div>` in `#transcript` using `frame.html` (or, for streaming text without html, a `<div class="assistant-text">` whose text content is updated). Sticky-bottom scroll.
- `on("state")` → update `#status` text + state dot class.
- `on("history_complete")` → mark initial paint done.
- Input box: Enter → `rpc("deliver",{handle, message})`, clear box.
- `Esc` (input blurred) → `rpc("interrupt_session",{handle})`.

**`index.html` / `base.css`:** the §"Window topology" layout from the parent spec, trimmed to single-tab: status line, transcript scroll area, growing input. Monospace transcript (`--aegis-fg` on `--aegis-bg`); kind colors from `var(--aegis-*)`. Diff rows green/red from `--aegis-ok`/`--aegis-err`.

**Acceptance:** no automated DOM test (no Playwright). Verified by the Task 5 live smoke. Self-check during authoring: `node --check` each JS file for syntax; assert `index.html` references resolve via the Task-1 route tests (extended to GET each referenced asset → 200).

Extend `tests/test_web_static_routes.py`: GET every asset referenced by `index.html` (`/static/js/ws.js`, `/static/js/app.js`, `/static/js/coalesce.js`, `/theme.css`, `/static/css/base.css`) → all 200. Commit.

---

### Task 4: `aegis web` CLI command

**Files:** Modify `src/aegis/cli.py`.

**Interfaces — Produces:** `aegis web [--cwd .] [--no-browser]` — resolves project root; ensures a `web:` token (generate + persist a 32-byte token into `.aegis.yaml` via the comment-preserving `aegis.config.edit` helpers if absent — mirror the existing token-write patterns); launches a co-resident `aegis serve` (reuse `_serve` with the web block forced on); prints the bookmark URL; opens the browser (`webbrowser.open`) unless `--no-browser`.

**Acceptance:** `aegis web --help` lists the command and flags (typer smoke). A unit test asserts the token-generation helper writes a `web.token` into a temp `.aegis.yaml` and is idempotent (reuses an existing token). Full serve launch is covered by the live smoke, not a unit test (it binds a port + spawns uvicorn).

TDD the token helper (`_ensure_web_token(root) -> str`); commit.

---

### Task 5: Live smoke (real agent, real browser)

**Not a file change — a verification gate.**

1. Add a `web:` block with a token to a scratch `.aegis.yaml` (or use `aegis web` to generate it) in a throwaway dir with a real agent profile (claude).
2. Launch `aegis web --no-browser` (background); capture the bound URL.
3. Drive a browser to the URL (saidkick → Alex's Chrome, with his go-ahead), spawn/auto-spawn the agent, send "reply with the single word: pong", confirm the transcript renders the streamed reply + the turn terminator, screenshot.
4. Tear down the serve process.

Report the screenshot + outcome. This is the end-to-end proof that the server-rendered `html` + streaming coalescing + input round-trip actually work in a real browser. (Per the standing rule, this test stays inline — not delegated.)

---

## Final verification

- [ ] `uv run pytest tests/test_web_server.py tests/test_web_static_routes.py -q` green.
- [ ] `node tests/web/coalesce.test.mjs` exits 0.
- [ ] `node --check` clean on all three JS modules.
- [ ] `uv run pytest -q -m "not live"` — no new regressions (known windowing flake aside).
- [ ] Live smoke screenshot shows a streamed agent reply in the browser.

## Self-Review

**Coverage:** routes + assets (T1, T3 route tests), pure coalesce logic (T2 node test), client behavior (T3 authoring + T5 smoke), CLI entry + token (T4). **Deferred to later slices (documented):** multi-tab/TabBar (S3), queue/group dashboards (S4/S5), theme switcher (S8), reconnect ring buffer (parent spec). **Testing honesty:** DOM rendering has no automated test in S2b (no Playwright); the node coalesce test + live smoke are the compensating controls, and the gap is stated, not hidden.
