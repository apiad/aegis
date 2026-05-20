# Aegis — Tasks / Next

Working roadmap for what's next. Curated public roadmap lives in
`docs/roadmap.md`; this file is the scratch/priority list.

## Shipped 2026-05-20

### Task queue v1 — **done**

Inter-agent delegation primitive shipped end-to-end (substrate + persistence
+ config + MCP plane + TUI integration + live smoke). See
`docs/superpowers/specs/2026-05-20-aegis-task-queue-design.html` and
`docs/superpowers/plans/2026-05-20-aegis-task-queue-v1.html`.

What's in:

- `aegis_enqueue(queue, payload, from_handle, callback=true)` and
  `aegis_task_status(task_id)` MCP tools.
- `QueueManager` (FIFO + max-parallel cap + substrate-deterministic
  dispatch; JSONL lifecycle log under `.aegis/state/queues/<queue>.jsonl`;
  restart replay marks in-flight as `failed:interrupted`).
- `InboxRouter` (per-handle delivery; wake-on-idle / mid-turn buffer /
  turn-end chain via `AgentSession.deliver`; JSONL writethrough under
  `.aegis/state/inboxes/<handle>.jsonl`).
- Universal sender tagging: queue callbacks, peer handoffs, Telegram, and
  the substrate all arrive at agent inboxes through one channel with a
  consistent `> from <sender> · …` header.
- `aegis_handoff` refactored to flow through the same `InboxRouter` —
  one delivery surface for everything an agent receives.
- `.aegis.py` grows `queues = {"<name>": {"agent": "<profile>",
  "max_parallel": N}, …}` with fail-loud validation at boot.
- TUI integration: queue workers appear as background tabs (no focus
  steal), per-pane inbox bind/unbind, `_SessionManagerAdapter` bridges
  Textual's async mount lifecycle to `QueueManager.spawn`'s sync seam.
- 196 hermetic tests + 1 live smoke covering the full chain.

### Delegation — **answered (subsumed by task queue)**

What we wrote for delegation: a deterministic substrate primitive
(`aegis_enqueue`) that returns a `task_id` immediately and, when the
spawned worker finishes, delivers the result back to the producer's
inbox as a normal user-message turn. Producer keeps working between
enqueue and callback arrival. Workers are ephemeral (one task per
spawn). Queues are statically configured in `.aegis.py`.

The brainstorm question "delegation = `spawn` + `handoff` (compose) vs.
delegation as its own primitive where the result auto-returns" was
answered by going with the second option: queues + callbacks form the
auto-return primitive; `aegis_handoff` stays as a separate
fire-and-forget peer-to-peer primitive (now riding the same inbox
channel for shape symmetry).

## Next up

### 1. Workflow scaffold v1 — **shipped (2026-05-20)**

`@workflow` decorator + auto-registry, `WorkflowEngine` runtime
(`delegate` / `send` / `drain` / `spawn` / `close` / `bash` / `log` /
`caller_handle`), `runner.run_workflow` with auto-drain + auto-close,
`aegis workflow list/run` CLI, and `aegis_run_workflow` MCP tool — all
composed on the v1 queue + inbox. Canonical example `examples/tdd_step.py`
plus a live e2e test (`tests/test_workflow_live.py`, marker `live`,
auto-skip when `claude` is off PATH) ride along. Plan:
`docs/superpowers/plans/2026-05-20-aegis-workflow-scaffold-v1.md`.

### 2. Queue v1 polish

Small, all on top of a shipped substrate:

- **Worker tab handle suffix** (T4.1 deferred) — `<handle> · <queue>#<task>`
  in the TUI tab bar so workers are visible at a glance. Touches
  `tui/widgets.py`, `tui/app.py`, `tui/pane.py`. Textual lifecycle was
  flagged as exploratory in the plan; should be straightforward now that
  the adapter is proven.
- **`aegis_cancel(task_id)` MCP tool** — currently cancellation flows
  through `aegis_handoff` to the worker's inbox; a dedicated tool would
  be cleaner.
- **`aegis_delegate` sync wrapper** — single MCP call that does
  enqueue + await internally for callers that want the simple sync
  shape. Composes on top of the existing primitives.
- **Telegram delivery sanity test** (T4.3 deferred) — verify the
  substrate header survives chunking and reaches the Telegram chat.

### 3. Sequential handoff (vision Phase 4)

Distinct from live handoff: agent A summarises its current task state
and *retires*; agent B (potentially a different harness) is instantiated
and continues from where A left off. Used for long tasks that need to
migrate (e.g. laptop → VPS) or where the original context window is
exhausted. Standalone, no dependencies on the workflow scaffold.

### 4. Long-lived bash terminals (vision Phase 4)

Bash sessions reified as named Aegis objects that agents can attach to,
observe, and inject into — replaces the Bash-as-tool-call pattern. The
`sender: terminal:<name>` slot is already reserved in the inbox tag
schema. Standalone.

## Watching

- **VPS job-crawler dispatched the plan job (2026-05-20-aegis-task-queue-plan)
  but never picked up its follow-up implement job** (file existed on VPS
  with `status: armed` and `fire_at` in the past, crawler was healthy
  and firing every 60s). One-off so far; needs a closer look at the
  crawler's eligibility logic if it happens again. Filing here, not
  acting on it yet.
