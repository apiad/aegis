# Aegis Web Client — S4 (Queue Dashboard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Builds on S3.

**Goal:** A `queue_digest` live stream + an always-on QueueStrip + a queue dashboard modal (Alt+D) showing queues and tasks (running / queued / recent), with click-to-jump to a worker's tab and a per-task assistant-text tail.

**Architecture:** The web frontend creates its own `QueueDigest` over the attached `QueueManager` (the TUI does the same; `aegis serve` doesn't yet). The `SubscriptionRegistry` gains queue subscribers + a `broadcast_queue_digest()`, fired on every QueueManager event. The frontend renders a compact strip (always-on when queues exist) and a modal; a `queue_tail` RPC fetches a task's tail on demand.

## Global Constraints

- Build on S3; existing tests stay green. No new deps.
- Queue surfaces are **read-only** (monitoring) — no enqueue from the web in S4.
- Gracefully no-op when no `queue_manager` is attached (strip + modal show "no queues").
- Keyboard: **Alt+D** opens the dashboard (Ctrl+D is browser-reserved, like the S3 chords).
- Commit straight to **main**. Conventional commits.

## File Structure

**Modified (backend):**
- `src/aegis/web/subscriptions.py` — `set_digest`, queue subscribers, `queue_digest_frame`/`broadcast_queue_digest`.
- `src/aegis/web/server.py` — create+start a `QueueDigest` from `manager.queue_manager`; subscribe its events to `broadcast_queue_digest`; store on registry.
- `src/aegis/web/wssession.py` — `subscribe(global queue_digest)`; `queue_tail` RPC.

**New (frontend):**
- `src/aegis/web/static/js/queues.js` — pure `formatStrip(queues)` (compact summary string parts) — node-tested.
- `tests/web/queues.test.mjs`.

**Modified (frontend):**
- `app.js` — subscribe queue_digest; render `#queuestrip`; QueueDashboard modal (Alt+D); jump-to-worker.
- `index.html` — `#queuestrip` element.
- `css/base.css` — strip + dashboard styles.

**Tests:**
- `tests/test_web_queue_digest.py` — broadcast + serialization + `queue_tail`.

---

### Task 1: queue_digest stream (backend)

**Registry:** `set_digest(digest)`, `subscribe_queue(sink)`, `unsubscribe_queue(sink)`,
`queue_digest_frame()` →
`{"type":"stream","kind":"queue_digest","queues":[asdict(QueueView)...],"tasks":[asdict(TaskView)...],"last_started": asdict|None}`
(empty lists when no digest), `broadcast_queue_digest()` → push frame to queue sinks.

**server.py (`build_web_app`):** after creating `registry`,
```python
qm = getattr(manager, "queue_manager", None)
if qm is not None:
    from aegis.queue import QueueDigest
    digest = QueueDigest(qm)
    digest.start()
    registry.set_digest(digest)
    qm.subscribe(lambda ev: registry.broadcast_queue_digest())
```
(The digest subscribes first in `start()`, so its state is fresh before the broadcast reads `snapshot()`.)

**wssession.py:** `_subscribe` global `queue_digest` → register `self._queue_sink`, send initial `queue_digest_frame()`; cleanup unsubscribes. New RPC `queue_tail{task_id}` → `{"lines": registry.queue_tail(task_id)}` (registry delegates to `digest.tail_of`). `supported_kinds` gains `queue_digest`.

**Tests (`test_web_queue_digest.py`):** a fake manager exposing a `queue_manager` with a real `QueueDigest`; drive a couple of QueueManager events (enqueue→start→complete via the real `QueueManager` or by feeding the digest's `_on_event`); a WSSession subscribed to `queue_digest` receives frames whose `queues`/`tasks` reflect the events; `queue_tail` returns recorded lines. Reuse the `FakeTransport` pattern.

TDD → commit.

---

### Task 2: `formatStrip` (pure, node-tested)

`formatStrip(queues)` → array of per-queue summary strings, e.g.
`"build ▸2 ⏳1 ✓5 ✗0"` (running ▸, queued ⏳, ok ✓, err ✗). Empty → `[]`.
Node test covers counts + empty. Commit.

---

### Task 3: QueueStrip + dashboard modal (frontend)

- `#queuestrip` between `#statusbar` and `#panes`; hidden (`display:none`) when `queues` empty, else shows `formatStrip` parts. Updated from `queue_digest` frames.
- **Alt+D** opens `QueueDashboard` modal: bands **QUEUES** (per-queue line), **IN-FLIGHT** (running tasks), **QUEUED**, **RECENT** (ok/err). Each task row: queue · payload_summary · worker_handle · state. A running task row with a `worker_handle` is clickable → if a tab exists for that handle, `activateTab`; else no-op. Clicking a task also fetches `queue_tail{task_id}` and shows the tail lines in a detail area. Esc closes (reuse modal stack).
- `app.js` keeps the latest digest in a module var; the modal renders from it and re-renders on new frames while open.

**Acceptance:** node + route tests green; `node --check` clean. DOM verified by the smoke.

Commit.

---

### Task 4: Visual smoke (with a live queue)

1. Smoke `.aegis.yaml` gains a `queues:` block (one queue → the opus agent).
2. Launch `aegis web`; in a tab, ask the agent to `aegis_enqueue` a task onto that queue (the serve's MCP plane is injected, so the agent can).
3. Open the dashboard (Alt+D / a button) → confirm the queue + the task appear; the strip shows counts; click the running task jumps to the worker tab.
4. Screenshot. Tear down.

(If driving the agent to enqueue proves flaky, fall back to confirming the dashboard opens and renders the empty/strip state — the data path is covered by Task 1's Python test.)

---

## Final verification
- [ ] `uv run pytest tests/test_web_*.py -q` + `-m "not live"` green.
- [ ] `node tests/web/*.test.mjs` exit 0; `node --check` clean.
- [ ] Smoke screenshot shows the queue dashboard.

## Self-Review
**Coverage:** digest stream + tail (T1), strip format (T2), strip+modal+jump (T3), end-to-end (T4). **Deferred:** ↑↓ keyboard nav within the modal (mouse-click jump suffices for S4); enqueue-from-web (out of scope — monitoring only). **No-queue** path no-ops gracefully.
