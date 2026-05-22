# Changelog

All notable changes to Aegis are documented here.
The format follows Keep a Changelog; this project uses SemVer (0.x).

## [Unreleased]

### Added
- **Live terminals.** Fifth coordination primitive: a real PTY-backed
  shell (bash or zsh) that any agent or Alex can spawn, run commands
  on, send raw keystrokes to, read history from, and subscribe to.
  Command boundaries are detected from [OSC 133 shell-integration
  markers](https://gitlab.freedesktop.org/Per_Bothner/specifications/blob/master/proposals/semantic-prompts.md);
  every finalized command is appended to a JSONL ledger and fires
  a `✉ from term:<name>` inbox notification (with cmd / exit code /
  duration / stdout tail) to every subscriber except the writer.
  Eight MCP tools (`aegis_term_spawn / list / run / keys / read /
  subscribe / unsubscribe / close`). TUI surface: `Ctrl+E` opens
  a `term:<name>` tab with per-command blocks; the input bar has
  `run` (Enter submits a command) and `raw` (`Ctrl+K` toggles —
  every keystroke goes straight to the PTY) modes. State at
  `.aegis/state/terminals/<name>/` (meta.json + ledger.jsonl +
  raw.log + shell rcfile); `aegis --resume` re-spawns saved
  terminals as fresh shells over their existing ledger, and any
  commands that were in flight are marked `killed_by_restart: true`.
  Spec: `docs/superpowers/specs/2026-05-21-live-terminals-design.md`.
  Docs: `docs/terminals.md`.
- **Workflow catalog v1.** Four seed workflows under `aegis.workflows`:
  `brainstorm_to_spec` (interactive Q/A → spec doc), `execute_plan`
  (parse plan markdown → dispatch implementer per task with durable
  resume), `review_branch` (parallel multi-reviewer fan-out → markdown
  report), `tdd_cycle` (three-phase predicate-driven loop). Engine
  gains `ask_human`, `spawn`/`close`, `checkpoint`/`resume_state`,
  `bash_predicate`, `parallel`, `config`, `host`, `workflow_id`.
  Runner becomes a long-lived class owning background workflow tasks
  with a JSONL ledger at `.aegis/state/<id>/`; `aegis_run_workflow`
  MCP tool is now non-blocking. New tools `aegis_workflow_status` and
  `aegis_workflow_cancel`; new CLI commands `aegis workflow status`
  and `aegis workflow cancel`. Spec:
  `docs/superpowers/specs/2026-05-22-workflow-catalog-design.md`.
  Docs: `docs/workflows.md`.
- **Session persistence.** `aegis` resumes the last workspace by default;
  `aegis --clean` opts out. Per-tab event logs + workspace.json live under
  `.aegis/state/`. Tabs whose drivers don't support session resume
  (currently Gemini, OpenCode) are skipped with a startup banner.
- **Shared canvas.** Third coordination primitive after queues and
  inbox handoffs: a markdown file multiple agents can read, write
  sections of, and subscribe to. Writes fire `✉ from canvas:<name>`
  inbox notifications to every other subscriber with diff math + a
  preview — same delivery channel as queue callbacks and handoffs,
  zero new TUI. Six MCP tools (`aegis_canvas_open / read /
  write_section / append_to_section / subscribe / unsubscribe /
  list`); section ownership is by convention only in v1, ledger
  records who wrote what. State at `.aegis/state/canvases/<name>/`;
  the markdown file lives wherever the caller points it. Spec:
  `docs/superpowers/specs/2026-05-21-shared-canvas-design.md`. Docs:
  `docs/canvas.md`.

## [0.4.0] - 2026-05-21

### Added
- **Queue dashboard.** Always-on one-line strip above every
  conversation's status bar (per-queue depth + most recent worker;
  adaptive format for 1 / 2–3 / 4+ queues) plus a `Ctrl+D` modal
  dashboard with `QUEUES / IN-FLIGHT / QUEUED / RECENT` bands and an
  inline `DetailPanel` showing payload, lifecycle, and a live
  assistant-text tail. `↑↓` move, `>` jumps to the worker's tab,
  `Esc` closes. Backed by a new `QueueDigest` aggregator subscribed
  to a push-based `QueueManager.subscribe()` hook (committed-state
  observability; observer exceptions never poison the substrate).
- **Inbox visibility in the TUI.** When a handoff, queue callback,
  Telegram message, or any other inbox message lands on an agent, the
  pane mounts a distinct `✉` block in the transcript before the agent
  reacts — sender / task / status / timestamp header plus up to 4 body
  lines (truncation footer if longer). New
  `AgentSession.on_inbox` observer slot fires synchronously on every
  `deliver()`, idle or mid-turn. Pure renderer
  `render.render_inbox_block(msg, colors)`.

### Fixed
- App-level `escape` priority binding no longer swallows modal-dismiss
  presses — `action_interrupt` dismisses a pushed `ModalScreen`
  before falling through to pane interrupt. Previously, pressing
  `Esc` to close the agent picker or queue dashboard was a silent
  no-op.
- Queue strip no longer sits flush against the model/permission
  status line — 1-row transparent margin separates the two panel
  bands.

## [0.3.0] - 2026-05-21

First public PyPI release as `aegis-harness`. Distribution name is
`aegis-harness`; the importable package is still `aegis`.

### Added
- **Multi-provider parity via ACP.** Gemini and OpenCode drivers rewritten
  on the official Agent Client Protocol Python SDK
  (`agent-client-protocol >= 0.10`). Multi-turn, streaming, cancellation,
  and per-session MCP injection are now identical across `claude-code`,
  `gemini`, and `opencode`.
- **Per-provider config classes** (`ClaudeCode`, `GeminiCLI`, `OpenCode`)
  in `aegis.config`. Legacy flat `Agent(harness=..., model=..., ...)`
  shape still works via a back-compat validator.
- **Task queues + workflows.** `aegis_enqueue` / `aegis_task_status` MCP
  tools, `QueueManager` (FIFO + max-parallel + substrate-deterministic
  dispatch + JSONL replay), `InboxRouter` with universal sender tagging,
  `@workflow` decorator + `WorkflowEngine` runtime, `aegis workflow
  list/run` CLI, `aegis_run_workflow` MCP tool.
- **Headless mode.** `aegis serve` runs SessionManager + MCP plane without
  a TUI, with an optional Telegram front-end (`/new`, `/close`,
  `/interrupt`, `/<handle> …`, bare-text routing). Configured via
  `telegram_token` / `telegram_chat_id` / `auto_add_to_telegram_prompt`
  in `.aegis.py`. systemd unit template at `scripts/aegis-serve.service`.
- **`aegis init` wizard.** Rich-powered interactive wizard that detects
  installed agent CLIs, walks through agent + queue setup, and refuses
  to clobber an upstream `.aegis.py` without `--force`.
- **TUI polish.** Per-block click-to-copy with hover tooltip, inline
  `WorkingIndicator` (spinner + rotating verb + elapsed timer) mounted
  inside the transcript, glued `ToolUse`↔`ToolResult` blocks,
  max-variety alliterating handle generation (no laureate or adjective
  reuse, letter cycling).
- **OIDC release workflow.** `.github/workflows/release.yml` publishes
  to PyPI on `v*` tag push using PyPI trusted publishing — no token
  stored in the repo.
- **Expanded docs.** New pages for Drivers, Queues, Workflows, the MCP
  plane, and an auto-generated API reference via mkdocstrings.

### Changed
- Distribution renamed from `aegis` to `aegis-harness` (the name `aegis`
  was already taken on PyPI). Import path is unchanged.
- README + docs site rewritten for the multi-provider surface; old
  Phase 1/1.5/2 framing replaced with a current-capability summary.
- Removed `legacy/` (sidelined FastMCP prototype) and `notes/`
  (scratch markdown). Git history preserves both.

### Fixed
- ACP driver: workaround for an upstream SDK race in `Connection.__init__`
  that was killing every Gemini/OpenCode session on startup.
- ACP driver: measure `duration_ms` locally in `send()` (the final
  status line was always showing 0.0s).

## [0.2.0] - 2026-05-18

### Added
- MCP plane (slice 1): a shared FastMCP HTTP server owned by aegis;
  spawned agents are injected strict + primed and get an `aegis_meta`
  orientation tool.
- MCP plane (slice 2): `aegis_list_sessions` / `aegis_list_agents` /
  `aegis_handoff` (fire-and-forget inter-agent context transfer);
  per-pane self-reported handle baked into the priming so each agent
  knows who it is and passes that as `from_handle`.

### Fixed
- Driver: large `tool_result` payloads (e.g. reading a SOUL.md-sized
  file) no longer silent-hang a turn. `create_subprocess_exec` now
  uses a 16 MiB `StreamReader` buffer (root cause: 64 KiB default was
  too small for legitimate lines), and `_pump_stdout` has a
  `try/finally` so the stream-closed sentinel always fires. Tool-result
  display is capped at 100 chars. Regression tests cover both
  guarantees.

## [0.1.0] - 2026-05-18

First tagged release — a usable, personal-infrastructure-grade meta-harness.

### Added
- CLI driver: runs Claude Code via `claude -p` stream-json (bidirectional,
  no log scraping); agent profiles from a Python `.aegis.py`.
- Full-screen Textual TUI replacing the line REPL.
- Multi-tab: N independent agent sessions, a sideways-scrolling tab bar,
  per-tab agent profiles, an `AgentPicker` modal, generated handles
  (`adjective-laureate`), cross-tab signalling (state dot + sticky `*` +
  bell).
- Theme engine (Textual-native) with the default **Ink** theme; themes are
  drop-in.
- Live status-line metrics: true input (incl. cache) with cached %, output,
  tool calls, turn / session time; provisional while streaming, exact at
  turn end.
- Lazy session start (harness spawns on first message, not tab open).
- `aegis --version`.

### Notes
- Not general-public-ready; runs from source via `uv`, drives a local
  `claude` CLI. The earlier FastMCP workflow-engine prototype is preserved
  under `legacy/`, unbuilt.
