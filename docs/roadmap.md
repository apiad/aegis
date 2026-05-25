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
  `.aegis.py` without `--force`.
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

### v0.6.0 (current)
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
