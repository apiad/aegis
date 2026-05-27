# Roadmap

## Shipped

### v0.1.0
- **Phase 1** — CLI driving Claude Code via stream-json.
- **Phase 1.5** — full-screen Textual TUI + live metrics.
- **Phase 2** — multi-tab + cross-tab signalling.
- **Polish** — generated handles, theme engine + Ink, lazy start,
  sideways tab scroll, honest cache-aware token metrics.

### v0.2.0
- **Phase 3 (slice 1)** — MCP plane foundation: shared FastMCP HTTP
  server owned by aegis; spawned agents injected strict + primed.
- **Phase 3 (slice 2)** — inter-agent tools: `aegis_list_sessions`,
  `aegis_list_agents`, `aegis_handoff`; per-pane self-reported handle.
- **Headless** — `aegis serve` + Telegram bridge.
- **Task queue v1** — `aegis_enqueue` + `aegis_task_status` MCP tools,
  `QueueManager` (FIFO + max-parallel cap + substrate-deterministic
  dispatch + JSONL replay), `InboxRouter` (per-handle delivery with
  universal sender tagging), `aegis_handoff` refactored through the
  same inbox channel.
- **Workflow scaffold v1** — `@workflow` decorator + auto-registry,
  `WorkflowEngine` runtime (delegate / send / drain / spawn / close /
  bash / log), `runner.run_workflow` with auto-drain + auto-close,
  `aegis workflow list/run` CLI, `aegis_run_workflow` MCP tool.

### v0.3.0
- **Multi-provider parity via ACP** — Gemini and OpenCode drivers
  rewritten on the official [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol)
  Python SDK. Multi-turn, streaming, cancellation, and per-session
  MCP injection now identical across all three providers.
- **TUI polish** — per-block click-to-copy with hover tooltip,
  inline `WorkingIndicator` (spinner + rotating verb + elapsed
  timer) mounted inside the transcript, glued ToolUse↔ToolResult
  blocks, max-variety alliterating handle generation.
- **Rich `aegis init` wizard** — detects installed CLIs, walks
  through agent + queue setup, refuses to clobber upstream
  `.aegis.yaml` without `--force`.
- **First PyPI release** — distributed as `aegis-harness`.

### v0.4.x (current)
- **Queue dashboard** — always-on one-line strip (per-queue depth,
  last in-flight worker); `Ctrl+D` full-screen modal with `QUEUES /
  IN-FLIGHT / QUEUED / RECENT` bands and a live assistant-text tail.
- **Session persistence** — `aegis` reopens the last workspace by
  default (tabs, profiles, order, with each underlying agent session
  genuinely resumed via provider-native resume APIs); `aegis --clean`
  opts out. Per-tab resume failures are contained and surfaced in-pane.
- **Shared canvas** — third coordination primitive. `aegis_canvas_*`
  MCP tools, file-backed markdown blackboard, per-section diff-aware
  notifications through the inbox channel.
- **Live terminals** — fourth coordination primitive in the inbox-
  delivery family. Real PTY-backed shells, OSC 133 shell integration
  for deterministic command-finish detection, eight `aegis_term_*`
  MCP tools, new `term:<name>` TUI tab type with command-block
  rendering and a `Ctrl+K` raw-key mode, session-scoped persistence
  with `killed_by_restart` sweep on resume.
- **Workflow catalog** — the `aegis.workflows` package: importing a
  workflow registers it. Four seeds ship: `brainstorm_to_spec` (Q/A
  → spec doc), `execute_plan` (parse plan → dispatch implementer per
  task with durable resume), `review_branch` (parallel reviewer
  fan-out → report), `tdd_cycle` (predicate-driven TDD loop). Engine
  gained `ask_human` (host-tab dialogue), `spawn`/`close` (subagent
  lifecycle), `checkpoint`/`resume_state` (explicit durability),
  `bash_predicate` (retry-with-feedback loop), `parallel` (fan-out
  join). `aegis_run_workflow` became non-blocking, joined by
  `aegis_workflow_status` and `aegis_workflow_cancel`.

### v0.11.0 (current)
- **Telegram renderer + correctness.** Worker replies now render
  through Telegram's HTML parse mode — fenced code, bold, italic,
  blockquotes, and links display natively instead of as literal
  backslashes. Greedy chunker; replies exceeding three parts spill
  to a `.md` attachment with a 500-char peek caption via a new
  `sendDocument` primitive. The status message becomes a live
  per-turn ticker that edits on tool-use boundaries instead of every
  2s — tool-call activity is visible in real time and the silent
  long-turn freeze (Telegram's rate-limit footgun) is gone. Multi-
  observer migration: TUI and Telegram both register via
  `add_event_observer` / `add_state_observer` / `add_inbox_observer`,
  so two frontends can observe the same session without clobbering.
  New `add_close_observer` on `AgentSession`; `_active` clears on
  every session-close path. Telegram update offset persists across
  restart. Tactical fixes: `send_message=None` guard, refresh-loop
  exceptions caught and logged.

### v0.10.0
- **Telegram substrate commands.** Nine new chat commands —
  `/queue list/show`, `/schedule list/show/run`, `/budget list/show`,
  `/peers`, `/help` — wired through a command registry. Cross-host
  via `@<peer>` syntax where the substrate already supports it
  (schedule + budget); queue + schedule-run are local-only this
  round. Existing five verbs (`/new`, `/close`, `/interrupt`,
  `/agents`, `/sessions`) migrated into the same registry; `/help`
  is now registry-driven.

### v0.9.0
- **Per-queue budgets.** Multi-window per-queue USD / output-token
  ceilings, all-must-allow, enforcement at enqueue time with a
  structured rejection naming the binding constraint and an
  `unblock_at` ETA. Cost computed from existing SessionMetrics via a
  static per-(provider, model) price table at
  `src/aegis/budget/prices.py`. Inspection via `aegis budget
  list/show`, `aegis_budget_status` MCP tool, and `GET
  /remote/v1/budget` on the plane. TUI surface deferred to v0.9.1.

### v0.8.0
- **Wire callbacks for remote queues.**
  `aegis_enqueue(target="<peer>", callback=True)` now delivers the
  remote worker's final message back to the originating agent's
  inbox over the wire. Symmetric peers config (both sides define
  each other in `remotes:`); `RemoteSpec` gains an optional
  `peer_name` field that controls the `callback_to` round-trip.
  Best-effort, no retry — every callback attempt is recorded in the
  receiver's queue JSONL.
- **Remote schedule control plane.** Five new endpoints under
  `/remote/v1/schedule` (PUT push, GET list/show, DELETE remove, GET
  logs); five matching `aegis_schedule_*` MCP tools with optional
  `target=` for cross-host; CLI `aegis schedule push --to <peer>`
  and a `--remote <peer>` flag on inspection verbs. Pushed schedules
  land in the receiver's `.aegis/schedules/<name>.yaml` overlay
  folder with a `# pushed_from:` provenance comment and become
  indistinguishable from native schedules under the v0.6 hot-reload
  watcher. Source classification (`inline` / `overlay` / `pushed`)
  is surfaced in list + show responses.

### v0.7.0
- **Remote plane.** Server-to-server enqueue over HTTP. `aegis serve`
  exposes a second HTTP plane (distinct from the loopback MCP plane)
  that other `aegis serve` instances can POST into; `aegis_enqueue`
  grows a `target=` parameter that routes the call to a configured
  remote's `/remote/v1/enqueue`. The remote runs the worker on its
  own filesystem under its own agent profiles. No wire return
  channel in v1 — completion behavior is whatever the receiving serve
  is configured to do. Two new `.aegis.yaml` sections — `remotes`
  (outbound peers, with optional bearer tokens) and `remote_plane`
  (inbound bind + token / IP allowlists). Recommended deployment
  binds the plane to a private overlay network (Tailscale, Headscale,
  WireGuard, VPN) so the network itself is the outermost trust
  boundary; HTTP-layer gates compose with AND on top. All failure
  paths are loud and distinguishable — no silent fallback to local
  enqueue. See [Remote plane](remote.md).

### v0.6.0
- **Scheduler substrate.** Cron-style scheduled workflow execution
  alongside QueueManager + InboxRouter. Declarative
  `.aegis.yaml` with drop-in overlays at `.aegis/schedules/<name>.yaml`;
  triggers (`cron`, `fire_at`), lifecycles (`forever`, `once`,
  `{fires: N}`, `{until}`), overlap policies (`skip`, `queue`, `kill`),
  and notify hooks. JSONL audit + snapshot per schedule. On-boot
  replay closes dangling fire-requested records. Hot reload via
  filesystem watcher: edit `.aegis.yaml` and the running scheduler
  atomic-swaps without restart.
- **Built-in workflows.** `prompt` (one-shot agent message) and
  `enqueue` (scheduler → queue handoff).
- **`aegis schedule` CLI.** `list / show / run / enable / disable /
  logs`; `enable`/`disable` go through a comment-preserving YAML
  editor.
- **Agent groups.** Sixth coordination primitive. Named committees
  with one in-flight broadcast slot, four-field broadcast contract
  (`objective` / `output_format` / `tool_guidance` / `boundaries`),
  `wait_all` + `wait_any` waiters with passive loser cancel, four
  built-in reducers (`concat`, `join_by_handle`, `last_wins`,
  `majority_vote`). Nine `aegis_group_*` MCP tools, mirror surface
  on `WorkflowEngine` plus `engine.ephemeral_group()` context
  manager, `.aegis.yaml` `groups:` + `.aegis/groups/<name>.yaml`
  overlays with preset-name fail-loud merge,
  `aegis_group_spawn_mixed(preset=...)` factory. Per-group JSONL
  audit at `.aegis/state/groups/<name>.jsonl` with on-boot replay.

## Next

- **Auto-checkpoint primitives.** Promote heavy primitives
  (`subagent`, `bash_predicate`, `parallel` joins) to auto-snapshot
  so authors don't write explicit checkpoints around them.
- **More catalog workflows.** `bug_repro`, `refactor_safely`,
  `ingest_to_wiki`, `distill_to_zettel`, `triage_issue`,
  `on_ci_failure`, `on_pr_opened`. Each new seed firms up the
  engine API.
- **Workflow visibility.** Status band in `Ctrl+D` dashboard
  (running / paused-on-ask_human / done / errored) alongside queues.
- **Multi-host distribution.** Laptop ↔ VPS session sharing via an
  `aegis-shelld` daemon. The terminal state layout was deliberately
  designed forward-compatible for this; it's the natural unlock for
  tmux-style real persistence of PTYs.
- **More drivers.** Codex, Aider, Cursor if/when they speak ACP.
- **Richer workflow primitives.** Parallel branches with named
  joins, durable replay (Temporal-style), spec-language workflows
  (markdown plan files that workflows execute step by step).

Aegis is personal-infrastructure-grade and evolves fast; expect
change before 1.0.
