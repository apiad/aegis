# Roadmap

Newest first. Patch releases bundle under their minor parent.

## Shipped

### v0.13.0 — 2026-05-27 *(current)*
- **MCP config-edit surface.** Spawned agents can mutate `.aegis.yaml`
  from inside through 12 new MCP tools (4 reads + 8 writes — add/remove
  agents, queues, plugin dirs, schedule toggles), going through the same
  comment-preserving validated atomic-write path as the `aegis config`
  CLI. Additive paths hot-register on the live `QueueManager` / agent
  map / plugin loader so an agent can declare a queue and enqueue to
  it within one `aegis serve` session.
- **Live context-size meter.** Status-line metrics gained
  `ctx Nk (P%)` segment — current turn's true input against the
  model's context window (Opus 4.x at 1M, Sonnet/Haiku at 200k,
  Gemini at 1M).

### v0.12.0 — 2026-05-27
- **`.aegis.yaml` is the single config substrate.** Legacy `.aegis.py`
  removed; legacy `aegis init` retired (bootstrap is now the TUI
  ConfigPanel, opened automatically in an empty directory).
- **`aegis config` CLI.** Scriptable, idempotent subcommands for every
  authorable section: `show / agent / queue / telegram /
  default-agent / plugin-dir`. Each writing verb routes through
  ruamel.yaml so comments survive, validates the prospective body via
  `yaml_loader.load_config` before persisting.
- **TUI ConfigPanel** (`F2`) — live `.aegis.yaml` editor with an
  AddAgent modal; same edit helpers back the CLI.

### v0.11.x — 2026-05-26
- **0.11.2 — File picker UX.** Background `FileIndexer` (watchdog +
  walk) starts on app load — picker opens instantly. `CopyableBlock`:
  click = copy, ctrl+click = open file from backtick token.
- **0.11.1 — File viewer/editor.** New `FileTab` TUI tab type with
  tree-sitter syntax highlighting; `Ctrl+O` fuzzy picker; clicking a
  backtick-wrapped token opens the picker pre-filled. MCP tool
  `aegis_view_file(path)` lets agents surface a file to the operator
  mid-task. VIEW mode default; EDIT mode toggled with `e`.
- **0.11.0 — Telegram renderer + correctness.** HTML parse mode
  (fenced code, bold, italic, blockquotes, links render natively).
  Greedy chunker; replies past three parts spill to a `.md`
  attachment via a new `sendDocument` primitive. Status message is
  a live per-turn ticker editing on tool-use boundaries instead of
  every 2s. Multi-observer migration: TUI and Telegram both register
  via `add_event_observer` / `add_state_observer` /
  `add_inbox_observer`.

### v0.10.0 — 2026-05-26
- **Telegram substrate commands.** Nine new chat commands
  (`/queue list/show`, `/schedule list/show/run`, `/budget list/show`,
  `/peers`, `/help`) wired through a command registry. Cross-host via
  `@<peer>` where the substrate supports it. The pre-existing five
  verbs (`/new`, `/close`, `/interrupt`, `/agents`, `/sessions`)
  migrated into the same registry.

### v0.9.0 — 2026-05-26
- **Per-queue budgets.** Multi-window USD / output-token ceilings,
  all-must-allow, enforced at enqueue time with a structured
  rejection naming the binding constraint and an `unblock_at` ETA.
  Cost computed from existing `SessionMetrics` via a static
  per-(provider, model) price table. Inspection via `aegis budget
  list/show`, `aegis_budget_status` MCP tool, and `GET
  /remote/v1/budget`.

### v0.8.x — 2026-05-25
- **0.8.1 — Wire-callback bug fixes.** `RemotePlaneSpec.peer_name`
  added; `aegis_enqueue(target=…)` defaults `callback` to False
  matching v0.7 semantics (True for local). Round-trip now works in
  the documented symmetric-peers configuration.
- **0.8.0 — Wire callbacks + remote schedule control plane.**
  `aegis_enqueue(target="<peer>", callback=True)` delivers the
  remote worker's final message back to the originating agent's
  inbox over HTTP. Five new endpoints under `/remote/v1/schedule`
  (PUT push / GET list/show / DELETE / GET logs) and five matching
  `aegis_schedule_*` MCP tools with optional `target=`. Pushed
  schedules land in the receiver's `.aegis/schedules/<name>.yaml`
  overlay folder with a `# pushed_from:` provenance comment.

### v0.7.x — 2026-05-25
- **0.7.1 — Remote-plane public surface rewrite.** Dropped the
  Telegram-as-default-return-channel framing; `callback_note` now
  reads honestly that v1 has no wire return channel. No code-behavior
  changes.
- **0.7.0 — Remote plane.** Server-to-server enqueue over HTTP.
  `aegis serve` exposes a second HTTP plane (distinct from the
  loopback MCP plane); `aegis_enqueue(target=…)` routes to a
  configured peer's `/remote/v1/enqueue`. Two new `.aegis.yaml`
  sections — `remotes` (outbound peers + optional bearer tokens) and
  `remote_plane` (inbound bind + token / IP allowlists). Recommended
  deployment binds the plane to a private overlay network so the
  network itself is the outermost trust boundary.

### v0.6.0 — 2026-05-25
- **Scheduler substrate.** Cron-style scheduled workflow execution
  alongside `QueueManager` + `InboxRouter`. Declarative
  `.aegis.yaml` with drop-in `.aegis/schedules/<name>.yaml`
  overlays; triggers (`cron`, `fire_at`), lifecycles (`forever`,
  `once`, `{fires: N}`, `{until}`), overlap policies (`skip`,
  `queue`, `kill`), notify hooks. JSONL audit + snapshot per
  schedule. On-boot replay closes dangling fire-requested records.
  Hot reload via filesystem watcher: edit `.aegis.yaml` and the
  running scheduler atomic-swaps without restart.
- **Built-in workflows.** `prompt` (one-shot agent message) and
  `enqueue` (scheduler → queue handoff).
- **`aegis schedule` CLI.** `list / show / run / enable / disable /
  logs`.
- **Agent groups.** Sixth coordination primitive. Named committees
  with one in-flight broadcast slot, four-field broadcast contract
  (`objective` / `output_format` / `tool_guidance` / `boundaries`),
  `wait_all` + `wait_any` waiters with passive loser cancel, four
  built-in reducers. Nine `aegis_group_*` MCP tools, mirror surface
  on `WorkflowEngine` plus `engine.ephemeral_group()` context
  manager.

### v0.5.x — 2026-05-23
- **0.5.1 — CI fix release.** v0.5.0 was tagged but never published
  to PyPI (two CI assertions failed against the new version string +
  ANSI escapes). 0.5.1 is the first PyPI release of the 0.5 line.
- **0.5.0 — Live terminals + workflow catalog v1.** Fifth
  coordination primitive: PTY-backed shells with OSC 133 command-
  boundary detection. Eight `aegis_term_*` MCP tools; TUI `term:<name>`
  tab with per-command blocks and a `Ctrl+K` raw-key mode.
  State at `.aegis/state/terminals/<name>/`; `aegis --resume`
  re-spawns saved terminals as fresh shells over their existing
  ledger. Workflow catalog: four seed workflows ship under
  `aegis.workflows` — `brainstorm_to_spec`, `execute_plan`,
  `review_branch`, `tdd_cycle`. Engine gained `ask_human`,
  `spawn`/`close`, `checkpoint`/`resume_state`, `bash_predicate`,
  `parallel`, `config`, `host`, `workflow_id`.

### v0.4.0 — 2026-05-21
- **Queue dashboard.** Always-on one-line strip (per-queue depth,
  last in-flight worker); `Ctrl+D` full-screen modal with `QUEUES /
  IN-FLIGHT / QUEUED / RECENT` bands and a live assistant-text tail.
- **Session persistence.** `aegis` reopens the last workspace by
  default (tabs, profiles, order, with each underlying agent session
  genuinely resumed via provider-native resume APIs); `aegis --clean`
  opts out.
- **Shared canvas.** Third coordination primitive. `aegis_canvas_*`
  MCP tools, file-backed markdown blackboard, per-section diff-aware
  notifications.

### v0.3.0 — 2026-05-21
- **Multi-provider parity via ACP.** Gemini and OpenCode drivers
  rewritten on the official [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol)
  Python SDK. Multi-turn, streaming, cancellation, and per-session
  MCP injection identical across all three providers.
- **TUI polish.** Per-block click-to-copy with hover tooltip; inline
  `WorkingIndicator` (spinner + rotating verb + elapsed timer) mounted
  inside the transcript; glued `ToolUse↔ToolResult` blocks.
- **First PyPI release** as `aegis-harness`.

### v0.2.0 — 2026-05-18
- **MCP plane.** Shared FastMCP HTTP server owned by aegis; spawned
  agents injected with strict MCP config + primed; per-pane
  self-reported handle. Inter-agent tools: `aegis_list_sessions`,
  `aegis_list_agents`, `aegis_handoff`.
- **Headless.** `aegis serve` + Telegram bridge.
- **Task queue v1.** `aegis_enqueue` + `aegis_task_status` MCP tools,
  `QueueManager` (FIFO + max-parallel cap + substrate-deterministic
  dispatch + JSONL replay), `InboxRouter` (per-handle delivery with
  universal sender tagging).
- **Workflow scaffold v1.** `@workflow` decorator + auto-registry,
  `WorkflowEngine` runtime (delegate / send / drain / spawn / close /
  bash / log), `runner.run_workflow` with auto-drain + auto-close,
  `aegis workflow list/run` CLI, `aegis_run_workflow` MCP tool.

### v0.1.0 — 2026-05-18
- **TUI foundation.** CLI driving Claude Code via stream-json; full-
  screen Textual app with multi-tab + cross-tab signalling; generated
  alliterating handles; theme engine + Ink default; lazy session
  start; sideways tab scroll; honest cache-aware token metrics.

## Next

### Time-sensitive (June 2026 billing changes)

- **Before June 15 — Claude REPL driver.** Anthropic splits
  interactive vs programmatic billing on June 15. `claude -p` (today's
  driver) hits the new metered credit pool from that date;
  interactive REPL stays on the subscription bucket. A second Claude
  driver (PTY + transcript JSONL) lands in parallel with the existing
  one. Design:
  [`2026-05-27-aegis-claude-repl-driver-design.md`](https://github.com/apiad/aegis/blob/main/docs/superpowers/specs/2026-05-27-aegis-claude-repl-driver-design.md);
  plan written, not yet executed.
- **Before June 18 — `GEMINI_API_KEY` support.** Gemini CLI's personal
  OAuth dies June 18 for AI Pro/Ultra accounts. Add optional
  `api_key` field to `GeminiCLI` profile; inject `GEMINI_API_KEY` into
  the subprocess env at spawn time. No driver changes; the subprocess
  picks up the env var.

### Designed, not yet built

- **Aegis filesystem tool surface.** Six aegis-owned tools
  (`aegis_bash`, `aegis_read`, `aegis_write`, `aegis_edit`,
  `aegis_grep`, `aegis_listdir`) routing every agent's file and shell
  access through the substrate. Harness-side suppression of built-in
  tools where the harness allows it; per-agent permission framework
  (`allow` / `deny` / `ask`) with TUI + Telegram routing. Design:
  [`2026-05-27-aegis-fs-tool-surface-design.md`](https://github.com/apiad/aegis/blob/main/docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md).
- **Agent sandbox.** Per-profile opt-in isolation primitives —
  worktree isolation, declarative read-only / hidden filesystem
  partitioning, outbound network block. Backend: `bubblewrap` for
  filesystem + network (Linux-only); native `git worktree add` for
  worktrees. Design:
  [`2026-05-27-agent-sandbox-design.md`](https://github.com/apiad/aegis/blob/main/docs/superpowers/specs/2026-05-27-agent-sandbox-design.md).

### Active polish

- **Worker tab handle suffix** (deferred from task-queue v1):
  `<handle> · <queue>#<task>` in the TUI tab bar so workers are
  visible at a glance.
- **`aegis_cancel(task_id)` MCP tool.** Today cancellation flows
  through `aegis_handoff` to the worker's inbox; a dedicated tool
  would be cleaner.
- **`aegis_delegate` sync wrapper.** One MCP call that does enqueue +
  await internally for callers that want the simple sync shape.
- **Telegram delivery sanity test.** Verify the substrate header
  survives chunking and reaches the chat.

### More drivers

- **Copilot ACP driver** (after June 1 Copilot billing transition).
  Copilot CLI supports ACP since Jan 2026: `copilot --acp` (stdio).
  Driver is a small `AcpDriver` shim — same shape as `GeminiDriver`.
  Auth via `gh auth login`.
- **OpenAI Codex JSON-RPC driver.** Codex CLI exposes a bidirectional
  JSON-RPC app server (`codex exec --json`). Different from ACP but
  documented and stable. Auth: `OPENAI_API_KEY`.
- **Antigravity CLI** (after Google's June 18 Gemini-CLI replacement).
  Probe for ACP support first; if confirmed, three-line shim. If not,
  stream-JSON probe and a custom parser.

### Larger arcs

- **Sequential handoff re-scope.** Original framing (vision Phase 4):
  agent A summarises its current task state and retires; agent B
  (potentially a different harness) is instantiated and continues
  from where A left off. Adjacent substrate has since shipped
  (workflow `send/drain/caller_handle`, visible inbox arrivals,
  canvas, agent groups, remote plane). Figure out what's left vs
  what's already in place before picking up.
- **Multi-host distribution.** Laptop ↔ VPS session sharing via an
  `aegis-shelld` daemon. The terminal-state on-disk layout was
  deliberately designed forward-compatible for this; it's the natural
  unlock for tmux-style real persistence of PTYs.
- **More catalog workflows.** `bug_repro`, `refactor_safely`,
  `ingest_to_wiki`, `distill_to_zettel`, `triage_issue`,
  `on_ci_failure`, `on_pr_opened`. Each new seed firms up the engine
  API.
- **Workflow visibility.** Status band in the `Ctrl+D` dashboard
  (running / paused-on-ask_human / done / errored) alongside queues.
- **Richer workflow primitives.** Parallel branches with named joins;
  durable replay (Temporal-style); spec-language workflows (markdown
  plan files that workflows execute step by step).
- **Auto-checkpoint primitives.** Promote heavy primitives
  (`subagent`, `bash_predicate`, `parallel` joins) to auto-snapshot
  so authors don't write explicit checkpoints around them.

---

Aegis is personal-infrastructure-grade and evolves fast. Expect
change before 1.0.
