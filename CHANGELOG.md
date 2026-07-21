# Changelog

All notable changes to Aegis are documented here.
The format follows Keep a Changelog; this project uses SemVer (0.x).

## [Unreleased]

### Working-indicator lifecycle + compact 'thought' blocks

- **Fixed: the working spinner could linger after Escape-interrupt.**
  `session.interrupt()` emits `(ready, finished=False)`, but the pane only
  removed the spinner on `finished=True` — so it was orphaned whenever no
  trailing `Result` arrived. The indicator is now reconciled to the live
  state (visible iff the agent is working), so interrupt clears it
  deterministically.
- **Fixed: the spinner didn't return on a self-woken / chained turn.** When a
  harness emitted events after `Result` (a background Monitor firing, an inbox
  wake), a lingering/frozen indicator made `_start_indicator()` no-op.
  `WorkingIndicator.start()` is now idempotent (cancels prior timers) and
  `_start_indicator()` always (re)starts a live one.
- **Reasoning blocks render as a compact summary.** A streamed thinking block
  now shows `💭 thought · 0:42 · ~1.2k tok` (elapsed + approximate token count,
  ~4 chars/token — the harness doesn't report thinking tokens) instead of a
  wall of reasoning text. The full reasoning stays in the block's copy payload.
  Replay matches (minus the duration, which history doesn't record).

### Process monitors — `aegis_monitor` (poll, visualize, auto-wake)

- **Wait on a long process without polling.** A new MCP tool `aegis_monitor`
  replaces the agent's `while …; sleep; tail` pattern. aegis does *not* own the
  process — the agent launches it (or it already runs, like a dev server) and
  hands aegis bash conditions evaluated on an interval in the session cwd:
  `done` (exit 0 ⇒ complete), optional `fail` (exit 0 ⇒ failed), optional
  `progress` (echoes 0–100 for a bar + ETA). A `timeout_s` backstop is terminal.
- **Auto-wake on the outcome.** On any terminal state the agent is woken via its
  inbox — the same `InboxRouter.deliver` path as queue callbacks — **interrupting
  its current turn if busy** so the notice lands immediately; an idle agent is
  woken directly. The agent fires the monitor, ends its turn, and continues when
  it completes — no turns burned polling.
- **TUI strip.** A `MonitorStrip` above the status bar (mirrors `QueueStrip`)
  shows one row per live monitor: `pytest ▓▓▓░░ 62% · ETA 0:18`, or
  `dev server ⣾ 0:42 watching` when there's no progress. Drops off on completion.
- **Surface.** `aegis_monitor`, `aegis_monitors` (list), `aegis_monitor_cancel`
  (no agent callback on cancel); monitors auto-reap on session close. The agent
  priming instructs *always* using this over sleep/tail loops. Backed by a new
  `MonitorManager` (poll loop + interrupt-if-working delivery), memory-only in
  v1. Remote mode shows the TUI host's monitors; web-client parity is a follow-up.

### Status-bar system meter

- **CPU / RAM / disk at a glance.** The status bar gains a
  `CPU 23% · RAM 38% · DSK 71%` segment — all system-wide percentages, sampled
  once per app tick (not per pane) from the local host. `DSK` reports the
  filesystem holding the project root (the disk agents write into). A metric
  turns amber past 90% so a hot machine catches the eye. Backed by `psutil`;
  remote mode shows the TUI host's figures (per-remote stats deferred).

## [v0.19.0] - 2026-07-20

### Dynamic Workflows — Track 2 JSON DSL

- **Agent-authorable dynamic workflows as a validated JSON document.** The
  safe/data counterpart to Track-1 durable `@workflow` Python: a JSON spec
  describing a fan-out/pipeline orchestration of **real aegis agents across
  harnesses**, licensed by schema validation (a malformed spec is rejected at
  the tool boundary; a valid spec is safe by construction). Where a single
  in-harness feature cannot, a dynamic workflow spawns any profile / hands to a
  live session / delegates to a queue, is **durable across process restarts and
  hosts**, and can **pause for the operator** mid-run.
- **Node families.** `sequence` + `agent` (with `target` = `spawn` | `session`
  | `queue`); `map` (bounded-concurrency fan-out via an `asyncio.Semaphore`) +
  `parallel` (barrier); `loop` (hard-bounded `max_rounds`) + `if` (branch
  routing) with typed `shell` (true iff exit 0) and `judge` (agent-call)
  predicates; a TUI-only `human` node via `ask_human` (an `enum` schema becomes
  selectable options).
- **Data flow.** `refs` selectors and templates resolve against a run-scoped
  `Store`; `agent` nodes take `inputs` substitution and a JSON-Schema `schema`
  that coerces structured output (prompt-engineered + `jsonschema`-validated,
  one bounded reparse-retry).
- **Durability.** Per-node checkpoint/resume — on resume only the in-flight
  node re-runs; `loop`/`if` decisions replay deterministically.
- **Semantic validator.** Rejects id collisions, selectors referencing an id
  not declared earlier in document order, an `agent` node with no `target` when
  there is no default agent, and unknown `spawn.profile` / `queue.queue`
  references — before the workflow runs.
- **Cost gate + MCP surface.** `aegis_run_dynamic_workflow` validates, gates,
  and launches; a plan-preview reports the projected agent count (a labelled
  static upper bound); the gate is operator-implicit or prompts the agent above
  a cost threshold (`dynamic_workflow_autoapprove_agents` config key). This is
  also the first landing of the Track-1 gating rule the design called out as
  missing.
- Spec: `docs/superpowers/specs/2026-07-17-aegis-json-dsl-dynamic-workflows-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-json-dsl-dynamic-workflows.md`.

### `aegis usage` — session usage & cost analytics

- **Read-only usage-and-cost dashboard over the session logs.** Aggregates the
  per-tab event streams already persisted to `.aegis/state/sessions/<handle>.jsonl`
  into a dashboard plus deeper cuts — temporal (usage over time), per-session,
  and per-tool — with segment-aware cost and token-split math and price
  resolution against the model registry.
- **`/usage` slash command (TUI + web).** Reuses the shared renderer so the
  in-app command and the `aegis usage` CLI surface identical figures.
- Spec: `docs/superpowers/specs/2026-07-17-aegis-usage-command-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-usage-command.md`.

## [v0.18.0] - 2026-07-17

### Slash commands 2D — command palette (drop-up typeahead)

- **Inline drop-up completion panel.** Typing `/` raises a panel above the
  input with fuzzy-matched commands and their one-line summaries; past the
  verb it completes subverbs and **live argument values** — agent slugs (with
  `harness · model · permission`), session handles (`agent · state`), queue /
  group / schedule / terminal names, theme ids, and flag names. A ghost usage
  hint shows the remaining arguments. Up/Down navigate, Tab/Enter accept, Esc
  dismisses; Enter with the panel closed submits as before.
- **One engine, two frontends.** A pure `complete(text, bridge) -> Completions`
  in the harness-agnostic commands core drives both the TUI (`CommandPalette`
  widget mounted above the input, with a `GrowingInput.key_interceptor` hook)
  and the web client (a `complete` WS RPC feeding a drop-up `<div>`), so both
  show identical candidates.
- **Completer seam.** `Arg` gains an optional `completer` (a static tuple or a
  `(bridge) -> choices` callable, each choice a value or `(value, detail)`
  pair). Subverbs are the first positional's completer; dynamic values are a
  later positional's bridge-driven completer. A new `fuzzy` scorer ranks
  matches. `complete()` never raises — a throwing completer contributes no
  items.

### Slash commands 2C — prompt commands + plugin `@command`

- **User-authored prompt commands.** `.aegis/commands/<name>.md` files register
  as `source=user` commands: frontmatter carries `description` / `argument-hint`,
  and the body is a template with `$1..$9` / `$ARGUMENTS` substitution, `@file`
  includes, and embedded `` !`shell` `` expansion (args-first, so a `$1` inside
  an `@file` or `` !`…` `` resolves before the include/shell runs). Expansion
  rides the `CommandResult.effect` `{"kind":"deliver"}` channel, so both the TUI
  and web seams send the rendered text to the agent as a normal message —
  Claude-Code slash-command parity.
- **Plugin `@command` decorator.** A `@command` decorator sits beside
  `@workflow` / `@hook` / `@tool`; commands defined in a plugin are
  auto-registered (`source=plugin`) on the plugin import sweep. An example ships
  in the bundled plugin.
- **Source precedence.** Both loaders plug into 2A's `source`-tagged registry,
  now with a full precedence rule in `register()` — **builtin > user > plugin** —
  so a user file shadows a plugin command of the same name but can never override
  a protected builtin. The 2D palette color-codes the three sources.
- **Boot-load, no live watch.** Prompt + plugin commands load at TUI `on_mount`
  and at `serve` boot; there is no filesystem watch in this slice.

### Slash commands 2B — full builtin coverage

- **Operator-useful builtins over the `AppBridge`.** New commands drive the
  meta-harness from the keyboard: `/groups` (list / status / dissolve),
  `/schedules` (list / show / enable / disable / remove / logs), `/terminals`
  (list / new / run / close), `/rename`, `/close`, `/themes`, and `/clear`.
  Agent management folds into `/agents` (`add` / `remove`); queue listing
  lands on `/queues` (**renamed from `/queue`** — 2A was unreleased, so no
  alias).
- **Conventions.** Collection nouns are plural (`/agents`, `/groups`,
  `/schedules`, `/terminals`, `/themes`, `/queues`); a bare noun-command is
  equivalent to its `list`.
- **Effect channel.** `CommandResult` gains an optional `effect` dict the
  frontend seams apply after rendering the block — `/themes <name>` switches
  the live theme (TUI `app.theme`, web stylesheet + localStorage) and
  `/clear` cosmetically wipes the transcript, leaving a marker that shows the
  context tokens still in play (the agent's context is untouched). Threaded
  through both the TUI pane and the web client.
- **Builtins are a package.** `commands/builtins.py` became a `builtins/`
  package (one module per command family). Added a `list_groups` method to the
  groups bridge.
- **Deferred / dropped.** `/model` and `/effort` (mid-session model/effort
  change) are deferred to a 2B.1 session-mutation slice — they require a
  resume-restart, not a thin call. `/handoff` is intentionally not a command
  (redundant with switching tabs for the operator; agent→agent handoff stays
  the MCP tool); `/config` is dropped (agent verbs live on `/agents`).

### Slash commands 2A — parser + resolution core

- **Declarative typed arguments.** Commands now declare an `ArgSpec`
  (positionals — required / optional / greedy — plus boolean and valued
  `--flags`); `dispatch()` parses the input against it and hands the handler a
  validated `Args`, replacing per-handler `.split()`. Flags are recognized
  anywhere among the non-greedy positionals (a boolean flag may lead or
  trail); a trailing greedy positional stops flag parsing so free-text
  (prompts) survives verbatim, `--x` and quotes included.
- **Protected builtins + command sources.** The registry tags each command
  with a `source` (`builtin` / `user` / `plugin`); a non-builtin command that
  collides with a builtin name is rejected (`CommandCollision`). `/help`
  groups by source. This is the seam the 2C user/plugin loaders plug into.
- **`//` literal-slash escape.** Typing `//foo` delivers a literal `/foo`
  message to the agent instead of running a command, via a shared
  `classify_input()` helper used by both the TUI and web seams.
- **`/queues new` persistence.** `/queues new <name> [agent]` writes to
  `.aegis.yaml` (comment-preserving, same path as `aegis config add-queue`)
  and hot-registers; `--ephemeral` keeps the old session-only behavior.
  (2B renamed `/queue` → `/queues`.)
- **Web parity.** The web `deliver` RPC routes `/command` through the same
  `dispatch()` and returns a `command_result` frame the client renders as a
  transcript block; `//` unescapes there too. The slash surface now works
  identically from the TUI and web input boxes.
- Spec/plan:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md`,
  `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2a.md`.

## [0.17.0] - 2026-07-16

### Slash commands + shell escape (TUI input)

- **`!<command>` shell escape.** Typing `!cmd` in the input runs `cmd` in a
  local shell (in the session's project root) and injects `$ cmd` plus its
  combined stdout/stderr into the conversation as your next message, so the
  agent sees the result. Output is capped, merges stderr, notes a non-zero
  exit, and times out after 60s. A bare `!` is a no-op.
- **`/<command>` slash commands (Phase 1).** Control commands aegis executes
  itself — a human-facing front-end over the same `AppBridge` surface agents
  drive through MCP; they never reach the harness. Results render as a
  `/`-glyph transcript block (red on failure); unknown commands point at
  `/help`. Builtins: `/help`, `/sessions`, `/agents`, `/spawn <agent>
  [prompt]`, `/queue new <name> [agent]`, `/enqueue <queue> <payload>`. The
  registry + pure `dispatch()` are harness-agnostic so the web client can
  reuse them. Spec:
  `docs/superpowers/specs/2026-07-16-aegis-slash-commands-design.md`.
- **Input-prefix accents.** While the input starts with `!` / `/`, its
  outline **and text** turn magenta / bright blue so a shell escape or command
  reads as distinct from a plain message (green) at a glance. Precedence:
  recording > shell-escape > slash-command > working > idle.

### Live terminals — correctness fixes

- **OSC 133 parser handles ST-terminated sequences**, not just BEL. Modern
  shell integrations (starship, VTE, Ghostty) terminate `]133;C` with ST
  (`ESC \`); the old BEL-only scan ran past it and **swallowed the following
  `]133;D` exit marker**, so `aegis_term_run` never saw command-end and always
  timed out on those machines. The parser now strips *all* OSC sequences (VTE
  / title / cwd noise no longer leaks into captured output), recognizes the
  `C` (output-start) marker, and returns an **ordered** output/event stream so
  a same-read `B…C…output…D` no longer wipes the output.
- **Marker-injection fallback** when shell integration is unavailable: aegis
  injects its own `printf` boundary markers so command-boundary + exit
  detection still work instead of hanging (the fallback the spec promised but
  never wired).
- **Bounded default `run()` timeout (120s)** so an interactive/never-returning
  command can't hang the per-terminal lock forever; a reader-loop crash now
  finalizes the pending command instead of stranding it.
- **Cleaner capture**: the echoed command line + prompt redraw are stripped
  (reset at the command-start / output-start markers).
- **Stops clobbering your shell**: array-aware prepend into `PROMPT_COMMAND`
  (starship / atuin / direnv / VTE render through it); zsh uses the
  `precmd_functions` / `preexec_functions` arrays instead of replacing hooks.
- **Multi-line commands rejected** with a clear error (each newline is a fresh
  prompt cycle → multiple `D` markers → corrupted correlation).

### Status-line tok/s meter

- The status line now carries a rolling **`⚡ N tok/s`** generation-speed
  segment (right after the `↓output` tokens): token-weighted output
  tokens/sec over the last 5 completed turns (`SessionMetrics.recent_tps()`,
  fed from a `(output, turn_seconds)` ring buffer on `commit`). Only real
  generation turns count — tool-only or instant turns are skipped — and the
  segment is hidden until the first such turn completes. Shows in both the TUI
  and the web status bar (both render from `SessionMetrics.render`).

### TUI input handling + handoff interrupt

- Richer chat-input gestures in `GrowingInput`: `Alt+Enter` (and `Ctrl+Enter`
  where the terminal distinguishes it) **send-with-interrupt** — cut the live
  turn and send the message now instead of queuing it behind the turn; `Esc`
  **clears a non-empty input** first and only interrupts the turn when the
  input is empty; `Up`/`Down` **recall previously-sent messages**
  (boundary-aware — active only at the first/last line so multi-line editing is
  intact — per-pane, session-lifetime, draft-preserving). `Enter` still
  enqueues; `Shift+Enter` / `Ctrl+J` still insert a newline (`Alt+Enter` moved
  off newline duty).
- The input outline now echoes the agent state dot: a **vivid green** outline
  when the agent is idle (live — your message acts immediately) versus a
  **subdued** outline while it is working (your message queues behind the
  turn), so you can tell at a glance whether you are writing against a live or
  busy agent. Voice recording still overrides with the amber outline.
- `aegis_handoff` gains `interrupt: bool = False`. `interrupt=True` cuts a busy
  peer's in-progress turn (via the new `AppBridge.interrupt(handle)`) before
  delivering, so the handoff lands as the peer's next turn instead of buffering
  — for blocking corrections a peer needs *now*. Returns `interrupted & landed
  at <target>` in that case. Default is byte-for-byte the old behavior.

### Web protocol v2 — compact-by-default + central persistence

- Events stream **compact-by-default**: heavy bodies are truncated on the
  wire (`ToolResult` clipped to a head, `ToolUse.raw_input` dropped,
  `AssistantThinking` body emptied) with a `truncated` flag; full detail is
  fetched on demand via the new `get_event` RPC. Foundation for the mobile /
  flaky-connection web PWA.
- `aegis serve`/web now persists sessions to JSONL like the TUI does
  (`SessionManager.attach_persistence`), so `seq` is a real disk line index in
  every frontend and web sessions survive a `serve` restart.
- `hello` advertises `protocol_version: 2` + `capabilities: ["compact"]`;
  `TOOL_RESULT_HEAD_LINES` / `TOOL_INPUT_HEAD_LINES` join the constants block.
- The web client renders transcripts **client-side** from the compact `event`
  payload (`renderEvent.js`, mirroring `render_html.py`); the server no longer
  ships a rendered `html` blob per event. Truncated blocks (tool input/output,
  thinking) expand on tap via `get_event`, cached per tab. This completes the
  wire diet — tool-heavy turns stream a fraction of the previous bytes.
- The web client is now an installable **PWA**: `manifest.webmanifest` + icon,
  a service worker that precaches the app shell and serves it cache-first
  (cache-busted per `server_version`), and a reconnecting banner for flaky
  links. Launches instantly and works installed; live actions require the
  connection (no offline outbox in v1).
- Mobile-first web layout: below 640px the client shows a session **list** and
  a full-screen **conversation** view (same DOM/state), with the composer
  pinned above the keyboard and a back control. **Swipe** left/right moves
  between agents. Desktop is unchanged; dashboards are desktop/TUI-only on
  mobile v1.

## [0.16.0] - 2026-06-26

### Unified input queue + click-to-dequeue chips

Every inbound message — text typed into the box and agent handoffs —
now flows through one inbox queue.

- `AgentSession.deliver` returns a `Delivery(landed | queued, depth)`
  receipt: `landed` when the agent was idle (consumed into a turn now),
  `queued` with its position when mid-turn. A new `on_dispatch` observer
  fires when a buffered batch starts its turn.
- Text-box input is no longer blocked while the agent works. It's
  delivered as a headerless `sender=user` message (a plain user turn)
  and, when the agent is mid-turn, shows as a click-to-dequeue **chip**
  above the input box (`PendingStrip`/`Chip`). Clicking a chip cancels
  that message before it reaches the agent. The input stays enabled —
  you can keep typing.
- `aegis_handoff` to a busy peer no longer rejects — it queues and
  returns `landed at <t>` or `queued for <t> (position N)`.

### Transcript windowing for long sessions

The TUI ConversationPane now keeps a bounded set of mounted blocks:
evicts the top when the count exceeds `N_MAX`, restores older blocks on
debounced scroll-up with anchor preservation, and trims the initial
replay so resumed long sessions start snappy. Sticky-bottom tracking
gates auto-scroll.

### pre_spawn hooks + socks-proxy plugin

- New `pre_spawn` hook event lets hooks rewrite a harness subprocess's
  argv/env before exec (wired in the Claude and ACP drivers).
- `socks-proxy` plugin tunnels harness subprocesses through
  `proxychains4`, built on the new `pre_spawn` seam.

### memory-system plugin (v0.1.0)

Second canonical plugin under `plugins/memory-system/`. Hermes-inspired
persistent memory:

- Per-project `.aegis/memory/` with `SOUL.md`, `USER.md`, and a
  `MEMORY.md` index over typed entries (`user` / `feedback` / `fact` /
  `reference`).
- `pre_turn` hook injects SOUL + USER + index + judgment primer on
  turn 0; top-5 entry teasers (name + description, 1000-word cap) on
  later turns.
- Five `@tool`s: `memory_add`, `memory_replace`, `memory_remove`,
  `memory_search`, `memory_read`.
- `dream` `@workflow` -- three-stage consolidate + synthesize pass over
  the last 7 days of `.aegis/state/sessions/`. Writes new entries +
  a dated `dreams/dream-YYYY-MM-DD.md` narrative log. Defaults to a
  Haiku-backed `dreamer` agent.
- Install optionally drops a daily 3am cron via
  `aegis.scheduler.push.write_atomic` (overlay file at
  `.aegis/schedules/memory-dream.yaml`); cron fires while `aegis serve`
  runs.

Proves the v1 plugin substrate generalizes beyond `skill-system` --
every primitive shape (`@hook`, `@tool`, `@workflow`) is exercised
end-to-end.

## [0.15.1] - 2026-05-29

The 0.15.0 release commit bumped `pyproject.toml` but didn't
regenerate `uv.lock`, so CI and the release workflow both failed on
`uv sync --locked` and nothing reached PyPI. This patch syncs the
lockfile to match the project version. No source-code or behavior
changes — 0.15.0 and 0.15.1 are byte-identical except for the lock.

## [0.15.0] - 2026-05-28

Two big lines land together: the **plugin substrate v1** (hooks +
tools + plugin install/uninstall + registry resolution + a canonical
`skill-system` plugin) and the **driver visibility parity arc** —
seven slices that close the gap on what Claude / Gemini / OpenCode
each publish on the wire, surfaced through one canonical event surface
the renderer treats identically.

### Plugin substrate (v1)

Five-slice plan landed end-to-end: hooks, tools, plugin
install/uninstall lifecycle, registry resolution, and a canonical
`skill-system` plugin.

- **Hooks** (`@hook`). `pre_turn` modifies the user message before it
  reaches the harness (`prepend_system`, `rewrite_user`, `block`);
  `post_turn`, `session_start`, `session_end` are observer hooks
  with per-hook timeout and JSONL logging. All four fire at the right
  point in `AgentSession`'s turn loop — `session_start` once at the
  top of the first turn, `session_end` on `close()`. Composition rules
  thread multiple `pre_turn` results deterministically.
- **Tools** (`@tool`). Decorator + registry with reserved-name guard,
  invocation wrapper that handles timeout / sync-or-async / JSONL
  logging, and FastMCP registration so every `@tool` lights up as an
  MCP tool the spawned agent can call.
- **Plugin lifecycle.** `plugin.toml` manifest parser,
  `InstallContext` for `_install.py` hooks, local-path install with
  rollback on failure, lockfile write/read, uninstall flow that calls
  `_uninstall.py` and strips config. `aegis plugin
  install/uninstall/list/show` typer subapp drives it from the CLI.
- **Registry resolution.** `gh:owner/repo[#path]` and `file://`
  registry URL parsers, `git archive` HTTPS fetch, install wired
  through registries; `aegis plugin update` + `aegis plugin search`
  round out the surface.
- **Canonical `skill-system` plugin** at `plugins/skill-system/`
  registers a `pre_turn` hook that lazy-loads filesystem skills + a
  `load_skill` MCP tool. Live integration test drives a real claude
  subprocess through the install → invoke loop.

### Driver visibility — parity slice 7 (SystemInit enrichment)

`SystemInit` carries optional `model`, `permission_mode`, `version`,
`available_commands` fields. Both substrates populate at boot:

- Claude `parse()` reads `system.init.model`,
  `system.init.permissionMode`, `system.init.claude_code_version`, and
  `system.init.slash_commands[].name`.
- `AcpSession.start()` emits a `SystemInit` immediately after
  `new_session` returns, with the agent name + version from
  `InitializeResponse.agent_info`. `AvailableCommandsUpdate` arrives
  later in the ACP timeline and surfaces as a follow-on `SystemInit`
  with only `available_commands` populated; consumers see two
  `SystemInit`s and can merge.

State `event_codec` round-trips every new field with
backward-compatible defaults.

This closes the 7-slice driver-visibility parity arc. The canonical
event surface now exposes every signal both substrates publish — what
the model is doing, what tools it invoked with what arguments on which
files with what diff, the running plan, mid-turn cost / mode / title
telemetry, and end-of-turn cost / stop_reason / per-model attribution.

### Driver visibility — parity slice 6 (mid-turn ContextUpdate)

New event types `ContextUpdate` + `CostUsage` are the canonical home
for mid-turn ACP telemetry that doesn't belong in the transcript:

- `UsageUpdate` (cost amount + context used/size) →
  `ContextUpdate(cost=CostUsage(…))`
- `CurrentModeUpdate` → `ContextUpdate(mode=…)`
- `SessionInfoUpdate` → `ContextUpdate(title=…)`

The renderer returns `None` for `ContextUpdate` so the pane skips it;
downstream subscribers (status bar, metrics observers) consume through
the standard event-observer surface — that wiring lands in a follow-on.
Claude has no equivalent — claude reports cost / size only at turn end
on `Result` (slice 5).

State `event_codec` round-trips cost/mode/title independently with
backward-compatible defaults.

### Driver visibility — parity slice 5 (Result enrichment)

`Result` event gains optional `stop_reason`, `ttft_ms`, `num_turns`,
`cost_usd`, `model_usage`, `permission_denials` fields. Each driver
populates whatever it surfaces (claude → all except cost is from
result.total_cost_usd; ACP → stop_reason from PromptResponse, cost
accumulated from mid-turn UsageUpdate.cost.amount, model_usage from
gemini's field_meta.quota.model_usage list).

The terminator line in the transcript grows from
`── done in 2.5s ──` to
`── done in 2.5s · $0.0042 · max_tokens ──` when the new fields fire.
Cost is shown only when > 0 (so claude-subscription turns stay
quiet) and `stop_reason` is shown only when non-default (so the
happy `end_turn` path stays silent).

State `event_codec` round-trips every new field with
backward-compatible defaults; legacy Result records still decode.

### Driver visibility — parity slice 4 (file-diff preview)

`ToolResult` now carries an optional `diff: (path, old_text, new_text)`
tuple that drivers populate for edit-shaped tool calls.

- ACP side: `session_update` walks `ToolCallProgress.content` for a
  `FileEditToolCallContent` block and captures `(path, old_text,
  new_text)` from it.
- Claude side: `ParserState` gains a `tool_diffs` dict; the `Edit`
  tool_use parser captures `(file_path, old_string, new_string)`, the
  `Write` parser captures `(file_path, "", content)` since Write
  overwrites. The matching tool_result attaches the cached diff.
- `render_event` shows a small unified preview when `diff` is
  populated and the call succeeded — trimmed common prefix/suffix,
  capped at 6 visible rows with a "… N more" footer, red `-` gutters
  for removed lines and green `+` for added. Failed edits still show
  the single-line `error …` form.

Real-CLI smoke against an opencode write of a 5-line file shows the
full added content live in the transcript:

```
✏️ write
  ┌ target.out
  │ + alpha
  │ + beta
  │ + gamma
  │ + delta
  │ + epsilon
  └
```

State `event_codec` round-trips the diff with backward-compatible
defaults.

### Driver visibility — parity slice 3 (AgentPlan blocks)

New canonical event `AgentPlan` + `PlanEntry` dataclass unifies
claude's `TodoWrite` tool input and ACP's `AgentPlanUpdate` notification.

- The claude `parse()` intercepts `tool_use(name="TodoWrite")` and
  emits `AgentPlan(entries=…)` instead of a generic `ToolUse`. Each
  entry's `content` + `status` (`pending` / `in_progress` /
  `completed`) flows through; priority defaults to `medium` (claude
  doesn't expose one).
- The ACP driver's `session_update` handler maps `AgentPlanUpdate` →
  `AgentPlan` with priority preserved.
- `render_event` grows a plan branch: `📋 Plan — N/M done` header
  followed by one row per entry with status glyphs (● completed,
  ◐ in_progress, ○ pending). High-priority entries bold their
  content; low-priority dim. Empty plans render `📋 (no plan)`.
- State `event_codec` round-trips with the natural shape; legacy
  records still decode.

Real-CLI smoke against an opencode planning turn: 4 distinct
`AgentPlan` events emitted (0/3 → 1/3 → 2/3 → 3/3 progress) with
proper glyphs interleaved with tool calls. The model's plan
revisions are now visible in real time instead of buried inside a
`⏺ TodoWrite(…)` blob.

### Driver visibility — parity slice 2 (chunk coalescing by message_id)

`AssistantText` / `AssistantThinking` now carry `message_id` (claude
from `assistant.message.id`, ACP from each chunk's `message_id`). A
new pure helper `aegis.render.coalesce_chunks` merges adjacent
same-`(type, message_id)` chunks into one event; any non-chunk event
(`ToolUse`, `ToolResult`, `Result`, …) breaks the run.

`replay_blocks` pipes its events through the coalescer before
rendering. An opencode session that persists ~116 thought-chunk
events under one message_id now renders as a single ✻ block on
resume instead of 116 separate lines. Live pane streaming was
unchanged — `_stream_append` already coalesced by kind for the
in-flight path.

Visible measurement (real opencode acp turn, file-read + write +
report):

- Raw events: **80**
- After coalesce: **9**

State `event_codec` round-trips `message_id` with backward-compatible
defaults; legacy persisted records still decode.

### Driver visibility — parity slice 1

Tool calls now read identically across all three drivers (claude
stream-json, gemini --acp, opencode acp) — every tool call carries
its semantic `kind`, `tool_call_id`, structured `raw_input`, and file
`locations`, and `ToolResult` correlates back via `tool_call_id` so
`kind` is available downstream. The TUI renderer picks a glyph by
kind (📖 read, ✏️ edit, ⌬ execute, 🔎 search, ✻ think, 🌐 fetch, ➡️
move, 🗑 delete, 🔄 switch_mode, ⏺ fallback) and shows a path tail
hint (`foo.py:42`) instead of the bare tool name.

Bug fixes:

- ACP failed tool calls were silently rendered as green ok lines —
  `is_error` is now derived from `ToolCallProgress.status == "failed"`.
- Gemini's PromptResponse usage was always zero because Gemini puts
  token counts in `field_meta.quota.token_count` (wire alias `_meta`),
  not in the canonical `usage` field — `AcpSession.send` now falls
  back to `field_meta.quota` when `usage` is None.

Spec: `docs/superpowers/specs/2026-05-28-aegis-driver-visibility-parity-design.md`.
Slice 2 (chunk aggregation by `message_id`) is next.

## [0.14.0] - 2026-05-28

### Workspace recovery: complete

`aegis` now restores the full previous workspace on relaunch — every
ConversationPane (claude-code via `claude --resume`; gemini and opencode
via ACP `loadSession`), every TerminalTab (re-spawned as a fresh shell
over its existing ledger), and every FileTab (re-opened at the saved
path). Both `Ctrl+Q` and crash exits persist a final snapshot so any
session_id latched mid-turn reaches disk and the next boot can resume.

Key wiring:

- `AegisApp.on_mount` loads `~/.aegis/state/workspace.json` once and
  threads the snapshot through `_resume_agent_tabs`, `_resume_terminals`,
  and `_resume_files` — so the default-spawn that fires when no agent
  tabs were resumable no longer clobbers terminals / files (a
  pre-existing bug that meant terminal-resume never actually worked).
- New `_boot_done` guard suppresses snapshot writes during the on_mount
  sequence so `self.theme = …` triggering `watch_theme → _refresh_tabbar`
  can't overwrite the saved roster before resume runs.
- `action_quit` now writes a final snapshot before teardown.
- `AcpDriver` advertises `supports_resume = True`; `AcpSession.start()`
  calls `conn.load_session(session_id=…)` instead of `new_session(…)`
  when a resume id is set. If the spawned agent doesn't implement
  `loadSession`, the resumed tab surfaces a clear ⚠ banner.
- New `WorkspaceFile(path, order, created_at)` schema entry; file
  tabs persist via `_write_snapshot` (filtering FileTab panes) and
  restore via the existing `_open_file_tab` path. Dirty buffers and
  cursor positions intentionally NOT preserved — file tabs are
  viewers, not long-lived sessions.

### Model registry: YAML-backed + auto-refresh

The hardcoded `aegis.budget.prices.PRICES` dict and the substring-pattern
`context_window_for` function are gone — both now derive from a single
canonical YAML at `src/aegis/data/models.yaml`, served by a new
`aegis.models` registry module. At CLI boot, `aegis` fires a best-effort
background fetch of
`https://raw.githubusercontent.com/apiad/aegis/main/src/aegis/data/models.yaml`
into `~/.cache/aegis/models.yaml` (24h TTL). The cache wins over the
bundled file on next load — so updating prices or adding new models is
a single PR to `main`, no release required. Cache failures (404, HTML
body, partial download) never corrupt the local copy: the fetcher
parse-validates before atomic replace, and a corrupt cache silently
falls back to the bundled YAML.

Public surface:

- `aegis.models.get_prices(provider, model)` — exact + alias match,
  raises `UnknownPriceError` on miss (preserves the legacy
  `prices.lookup` contract).
- `aegis.models.get_context_window(harness, model)` — exact, then
  alias, then `context_window_patterns` substring fallback, then
  provider default; 0 for unknown providers.
- `aegis.models.models_for(provider)` — `(name, label)` pairs powering
  the picker.
- `aegis.budget.prices` is a thin backward-compat shim over the
  registry; existing callers (`cost.compute`, queue manager, budget
  evaluator) keep working unchanged.

### Registry-backed model picker in `AddAgentModal`

The Add-Agent modal's model field is now a `Select` populated from
`aegis.models.models_for(<provider>)`. Switching providers repopulates
the dropdown; picking `<custom>` reveals an Input for any arbitrary
model name. `ModelEntry` gains an `aliases` list (so `claude-opus-4-7`
and `opus` resolve to the same prices) and an optional `label` for the
"opus → claude-opus-4-7" picker subtitle.

### Refresh tooling

- `scripts/refresh-models.py` regenerates `models.yaml` from
  `https://models.dev/api.json` (the catalog OpenCode itself consults
  per opencode.ai/docs/models). Curation lives at the top of the script
  (CLAUDE_CODE / GEMINI / OPENCODE lists). `--diff` previews,
  `--apply` writes.
- `aegis models refresh` synchronously refetches the GitHub raw URL +
  reloads the in-memory registry (use when you don't want to wait for
  the 24h background TTL). `aegis models clear` deletes the local
  cache. `aegis models list [provider]` prints exactly what aegis
  currently sees.

### Model catalog corrections

The bundled catalog regenerated from models.dev surfaces several
inaccuracies in the prior hardcoded table:

- **Claude Opus 4.7 is $5 / $25 per MTok**, not the legacy Opus 4.1
  $15 / $75. A 3× cost-reporting error in earlier 0.13.x sessions.
- **Claude Sonnet 4.6 has a 1M context window**, not 200k.
- **Gemini lineup:** `gemini-3-pro-preview`, `gemini-3.5-flash`,
  `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-3.1-flash-lite` —
  model IDs match what `ai.google.dev` publishes.
- **OpenCode** entries now use the `<vendor>/<model-id>` form opencode
  writes in its own config, sourced from the same models.dev provider
  IDs: `anthropic/claude-{opus-4-7, sonnet-4-6, haiku-4-5}`,
  `google/gemini-{3-pro-preview, 3.5-flash, 2.5-pro, 2.5-flash}`,
  `moonshotai/{kimi-k2.6, kimi-k2-thinking, kimi-k2-0905-preview}`,
  `minimax/MiniMax-{M2.7, M2.1, M2}`, `deepseek/deepseek-{v4-pro,
  v4-flash, chat, reasoner}`, `alibaba/{qwen3.7-max, qwen3-coder-plus,
  qwen3.6-plus}`.

The pre-existing bare `kimi-k2.6` slug is preserved as an alias of
`moonshotai/kimi-k2.6` so existing `.aegis.yaml` files don't break.

Parser hardening: `cache_hit` / `cache_write` are now optional in the
YAML (many providers don't publish them); missing fields default to 0
and `thinking` falls back to `output`. Pre-fix, parsing raised
`KeyError` on Moonshot / MiniMax / DeepSeek rows that omit
`cache_write`.

### Live cost segment in the status line

The status line gains a USD cost segment between the ctx % and the
tool counter, recomputed every render from the running token tallies
against the rate card. Adaptive formatting keeps it short:

```
↑12.0k (45% cached) ↓3.1k · ctx 12k (6%) · 12.3¢ · ⚒ 4 · 3s / 1m12s
↑1.2M (60% cached) ↓45k · ctx 1.2M (60%) · $5.43 · ⚒ 28 · 4s / 18m02s
```

Sub-cent → `X.Y¢`, 1¢–99¢ → `N¢`, ≥$1 → `$X.XX`. Unknown model or
lookup failure drops the segment silently.

`SessionMetrics` gains `c_cache_write` (tracking `cache_creation`
tokens separately from `cache_read`, billed at different rates) plus
`provider` / `model` strings that drive the lookup, and five
`@property` accessors mapping internal counters to the attribute
names `aegis.budget.cost.compute()` reads — so the same registry
path powers both per-turn budget enforcement and the live status
line.

### Fixes

- **ACP usage mapping.** Gemini and OpenCode sessions were rendering
  0 / 0 / 0 / 0 for every status-line metric: the driver populated
  `Result.input_tokens` / `Result.output_tokens` as bare fields from
  the legacy `field_meta["quota"]["token_count"]` path but never set
  `Result.usage`, and `SessionMetrics.commit` reads `ev.usage`
  exclusively. The ACP SDK has had a structured
  `PromptResponse.usage` (`acp.schema.Usage`) with
  `input_tokens` / `output_tokens` / `cached_read_tokens` /
  `cached_write_tokens` / `thought_tokens` since the protocol added
  it — the driver now reads it directly and builds a `TokenUsage`
  with `thought_tokens` folded into `output` (every provider aegis
  surfaces today bills thinking at the output rate).

## [0.13.0] - 2026-05-27

### MCP config-edit surface

Spawned agents can now mutate `.aegis.yaml` through the same
comment-preserving, validated, atomic-write path the `aegis config`
CLI uses — 12 new MCP tools surfaced uniformly to every spawned
harness. Additive paths hot-register on the live `QueueManager` /
agent map / plugin loader so the "create queue → enqueue to it" loop
works within a single `aegis serve` session; removes persist to YAML
but defer effect to next restart, signalled via the return-value
`restart_required_for` field.

Read tools (4):

- `aegis_config_show` — full parsed `.aegis.yaml`; telegram token
  redacted to `<set>`/`<unset>`.
- `aegis_config_list_agents` — `[{slug, harness, model, effort,
  permission}, …]`.
- `aegis_config_list_queues` — `[{name, agent, max_parallel,
  budgets}, …]`.
- `aegis_config_list_schedules` — `[{name, cron, enabled, workflow},
  …]`.

Write tools (8) — all wrapping the corresponding `aegis.config.edit`
helper:

- `aegis_config_add_agent(slug, harness, model, effort?, permission?)`
  — **live** (registers on the agent map).
- `aegis_config_remove_agent(slug)` — persisted; restart required.
- `aegis_config_add_queue(name, agent, max_parallel, budgets?)` —
  **live** (registers on `QueueManager`).
- `aegis_config_remove_queue(name)` — persisted; restart required.
- `aegis_config_add_plugin_dir(path)` — **live** (re-runs
  `import_plugins` so any new `@workflow` registers immediately).
- `aegis_config_remove_plugin_dir(path)` — persisted; restart
  required.
- `aegis_config_set_schedule_enabled(name, enabled)` /
  `aegis_config_toggle_schedule_enabled(name)` — live; the existing
  `ReloadWatcher` picks the change up.

Every write returns
`{ok, live, restart_required_for, [note]}`. Validation failures
(unknown harness, duplicate slug, queue referencing a missing agent)
bubble up as `{error: ...}` with the same wording the human sees at
`aegis config …`. A per-server `asyncio.Lock` serializes writes; the
existing `_atomic_write` (tempfile + rename) keeps the on-disk file
well-formed under concurrent calls. Out of scope in v1:
`set_telegram`, `set_default_agent`, fully-live removes, dry-run
mode, and groups/remotes (no `aegis.config.edit` helpers yet).

Spec: `docs/superpowers/specs/2026-05-27-mcp-config-edit-design.md`.
Plan: `docs/superpowers/plans/2026-05-27-mcp-config-edit.md`.

### Live context-size meter

The status-line metrics widget gained a `ctx Nk (P%)` segment showing
the most recent turn's authoritative `true_input` against the model's
context window, alongside the existing cumulative `↑` total. While a
turn is in flight, `p_in` (the monotonic max of streamed assistant
usages) drives the live value; at turn end the committed
`result.usage.true_input` takes over. Window comes from a hardcoded
`context_window_for(harness, model)` map — Claude Opus 4.x at 1M,
Sonnet/Haiku 4.x at 200k, Gemini at 1M, anything with `1m` in the
model name at 1M, OpenCode 200k. Unknown harness suppresses the
segment.

## [0.12.0] - 2026-05-27

### BREAKING

- **`.aegis.py` removed.** `.aegis.yaml` is now the single config
  substrate. Migration: rewrite your imperative `Agent(...)` /
  `queues = {...}` / `telegram_token = ...` lines as YAML sections
  (see [Configuration](docs/configuration.md)). `find_project_root`
  keys off `.aegis.yaml`; any `.aegis.py` in the tree is ignored.
- **`aegis init` retired.** Bootstrap paths now: launch `aegis` in
  an empty directory (the TUI opens the ConfigPanel — press `a` to
  add an agent), or use the scriptable CLI verbs (`aegis config
  agent add <slug> --provider <…> --model <…>` writes a minimal
  `.aegis.yaml`).

### `aegis config` CLI surface

Scriptable, idempotent subcommands for every authorable section of
`.aegis.yaml`. Each writing verb routes through ruamel.yaml so
existing comments and key order are preserved, validates the
prospective body via `yaml_loader.load_config` before persisting, and
fails loud on invalid input (the on-disk file is unchanged):

- `aegis config show [--json]`
- `aegis config agent list / add <slug> --provider --model
                                       [--effort] [--permission] /
                            remove <slug>`
- `aegis config queue list / add <name> --agent --max-parallel
                                       [--budget …]+ /
                            remove <name>`
- `aegis config telegram show / set [--token --chat-id --auto-prompt
                                    + matching --clear-* variants]`
- `aegis config default-agent <slug>`
- `aegis config plugin-dir list / add / remove`

`--budget` format: `usd:1.00:1h` or `output_tokens:500000:1h`
(repeatable).

### TUI ConfigPanel

New tab type alongside `ConversationPane` / `FileTab` / `TerminalTab`.
Stacks four sections — default-agent + agents table, queues table,
telegram block (token redacted), plugin_dirs list — and re-reads
`.aegis.yaml` on each refresh.

- **Boot-into-panel.** Launching `aegis` in a directory with no
  `.aegis.yaml` no longer refuses to start. The TUI mounts the panel
  as the only tab, status bar nudges you to add an agent.
- **Mid-session.** `F2` opens (or focuses) the panel from any other
  tab. `Ctrl+,` was the original binding but most terminals don't
  deliver it distinctly from `,`.
- **AddAgentModal.** Press `a` on the panel → modal with
  slug/provider/model/effort/permission fields, validates through
  the same `add_agent` helper the CLI uses, refreshes on save.

### File picker — keyboard nav + bypass on unique match

- Up/Down/PgUp/PgDn move the highlight while focus stays in the
  Input (priority bindings).
- Top match is always preselected after each filter pass — Enter
  opens it without arrow keys.
- Escape is now a priority binding so the Input can't swallow it.
- Indexer poll is one-shot; was running every 150ms forever and
  clobbering your typed query.
- Ctrl+click on a backtick token bypasses the picker entirely when
  the token resolves to a unique indexed file (otherwise falls back
  to the prefilled picker, whose dismiss path now actually opens
  the file — the previous `push_screen` call dropped the result).

### File viewer — cancel-edit confirm bar

Escape in edit mode with unsaved modifications now shows a
`⚠ unsaved edits — [d] discard / [esc] keep editing` bar and parks
the TextArea read-only so the bar's keystrokes don't get typed into
the buffer. Clean buffer still exits edit mode silently.

## [0.11.2] - 2026-05-26

### File picker improvements

- Background `FileIndexer` (watchdog + `os.walk`) starts on app load — picker
  opens instantly instead of blocking on `rglob`. Ships its own comprehensive
  ignore list (`.git`, `__pycache__`, `.venv`, `node_modules`, `*.pyc`, etc.);
  does not parse `.gitignore`. Live-updates as agents create or delete files.
- `FilePickerModal` reads from `FileIndexer` when available; falls back to
  synchronous walk in test environments without a full `AegisApp`.
- `CopyableBlock`: click = copy text (restored); ctrl+click = open file from
  backtick token. Multiple tokens → `_TokenChooser` lets you pick which one.
  Tooltip updated to `"click to copy | ctrl+click to open file"` when tokens
  are present.

## [0.11.1] - 2026-05-26

### File viewer/editor

- `FileTab` — new TUI tab type for viewing and lightly editing any file with
  syntax highlighting (tree-sitter via `textual[syntax]`).
- Ctrl+O opens a fuzzy `FilePickerModal` with typeahead over the current
  working directory (up to 5000 entries).
- Clicking any backtick-wrapped token in an agent response opens the file
  picker pre-filled with that token.
- MCP tool `aegis_view_file(path)` lets agents surface a file to the operator
  mid-task; focuses an existing tab if the same path is already open.
- VIEW mode (default): read-only; 2s mtime polling auto-reloads on disk
  changes.
- EDIT mode (`e`): writable; disk changes show a warning bar with `[r]` reload
  / `[k]` keep options; Ctrl+S saves; Esc returns to VIEW.

## [0.11.0] - 2026-05-26

### Telegram renderer + correctness (buckets B+D from the v0.10 critique)

- Replace MarkdownV2-escape-everything render path with HTML parse mode.
  Worker replies with fenced code, bold, italic, blockquotes, links now
  render natively instead of as literal backslashes.
- Greedy chunker; replies >3 parts spill to a `.md` attachment with a
  500-char peek caption (uses new `sendDocument`).
- Status message becomes a live per-turn ticker — edits on tool-use
  boundaries instead of every 2s. Tool-call activity is now visible.
- Multi-observer migration: TUI and Telegram both register via
  `add_event_observer` / `add_state_observer`; two frontends can
  observe the same session without clobbering.
- New `add_close_observer` on `AgentSession`; `_active` clears on any
  session-close path.
- Telegram update offset persists across restart.
- Tactical fixes: send_message=None guard, refresh-loop exceptions
  caught and logged.

### New dependency
- `markdown-it-py>=3.0`

## [0.10.0] - 2026-05-26

### Added
- **Telegram substrate command surface.** Nine new chat commands
  reach every existing substrate from the phone:
  - `/queue list` + `/queue show <name>` — local-only (no cross-host
    queue endpoint yet).
  - `/schedule list [@peer]` + `/schedule show <name> [@peer]` +
    `/schedule run <name>` (local-only fire-now).
  - `/budget list [@peer]` + `/budget show <queue> [@peer]`.
  - `/peers` — list configured remotes with reachability probe.
  - `/help` + `/help <name>` — registry-driven.
- **Command registry** in `src/aegis/telegram/commands.py`. The five
  existing verbs (`/new`, `/close`, `/interrupt`, `/agents`,
  `/sessions`) migrated into the same registry; single source of
  truth for `/help`.
- **`@<peer>` cross-host syntax** parsed by the dispatcher. Each
  handler decides whether to honor it; commands that don't support
  cross-host return a clear error.
- **Plain-text output by default; tabular data in fenced code
  blocks** for proper monospace alignment on mobile. No
  MarkdownV2-escape gymnastics in any new command.

### Changed
- `TelegramFrontend.__init__` grows `bridge` and `cfg` positional
  params. Existing `aegis serve` wire-up updated; no external API
  change.

Spec: `docs/superpowers/specs/2026-05-26-aegis-telegram-substrate-commands-design.md`.

## [0.9.0] - 2026-05-26

### Added
- **Per-queue budgets.** Each queue may declare one or more
  `(constraint, window)` ceilings (USD or output-token) over a
  rolling window. New `aegis_enqueue` calls are rejected with a
  structured error when admitting the task would push the queue
  over any of the declared budgets; ALL budgets must allow. Rejection
  names every blocked constraint and an `unblock_at` ETA.
- **Cost accounting.** Existing per-queue JSONL audit now carries a
  `cost` field on every `completed` and `failed` record:
  `{usd, input_tokens, output_tokens, cache_hit_tokens,
  cache_write_tokens, thinking_tokens}` computed from
  `SessionMetrics` (committed c_in/c_out/c_cached counters) +
  a static per-(provider, model) price table at
  `src/aegis/budget/prices.py`. Unknown models record
  `cost: {error: "unknown_model"}` without crashing the finalizer.
  Failed workers count toward budget — they burned tokens too.
- **`BudgetExceeded` typed exception** for the workflow engine:
  `engine.enqueue` raises with the full Decision attached so
  workflow Python can choose a retry strategy.
- **`aegis_budget_status` MCP tool** with `target=None` local and
  `target="<peer>"` cross-host via the new `GET /remote/v1/budget`
  and `GET /remote/v1/budget/<queue>` HTTP endpoints.
- **`aegis budget` CLI** — `list` (one-line summary per queue) and
  `show <queue>` (full Decision with per-budget rows). `--remote
  <peer>` on both.

The TUI strip + dashboard band described in the spec are
**deferred to v0.9.1**.

Spec: `docs/superpowers/specs/2026-05-25-aegis-per-queue-budgets-design.md`.

## [0.8.1] - 2026-05-25

### Fixed

Three issues in the v0.8.0 wire-callback path that together prevented
callbacks from working in any documented configuration:

- **`RemotePlaneSpec` now carries a `peer_name` field.** v0.8.0
  `cli.py` read it via `getattr(..., None) or "this-serve"`, but the
  dataclass had no such field — so every outbound callback identified
  the sender as the literal string `"this-serve"`, which no real
  receiver's `remotes:` map names. Round-trip was 100% miss.
- **`aegis_enqueue(target=…)` now defaults `callback` to False**
  (matching v0.7 fire-and-forget semantics). v0.8.0 made it default
  True, which silently broke pre-existing agent prompts that called
  the tool against a remote target without specifying callback —
  those calls began returning an error when this serve had no
  `remote_plane` configured. The signature also widened from
  `callback: bool = True` to `callback: bool | None = None` so the
  default can be context-sensitive (True for local, False for
  remote).
- **Loud rejection at MCP-tool boundary** when `callback=True` is
  set on a remote target and any of `remote_plane.peer_name` /
  `remotes[target].peer_name` / `remote_plane` block is missing.
  v0.8.0 silently sent `callback_to=None` on the wire in those cases
  and the receiver's observer dropped without error.

Also:

- **Callback observer now holds a strong reference** to every
  in-flight `asyncio.create_task` and discards on completion via
  `add_done_callback(set.discard)`. v0.8.0 fire-and-forget tasks
  could be garbage-collected mid-await under burst load, dropping
  callbacks without a log line. (Python docs explicitly warn about
  this pattern.)

Deployment behavior: fire-and-forget enqueues continue to work
unchanged. Callback-using deployments need `remote_plane.peer_name`
set in `.aegis.yaml` on the caller's side (and the matching
`remotes.<peer>.peer_name` on the caller's side already; symmetric
on the receiver). The MCP-tool error returned when something is
missing now points at the exact missing field.

## [0.8.0] - 2026-05-25

### Added
- **Wire callbacks for remote queues.** `aegis_enqueue(target=…, callback=True)` now actually delivers the worker's final message to the originating agent's inbox once the remote task terminates. Symmetric peers config (both sides define each other in `remotes:`); RemoteSpec gains an optional `peer_name` field that controls the `callback_to` round-trip. Best-effort, no retry, log+drop on miss; receiver's queue JSONL records every callback attempt.
- **Remote schedule control plane.** Five new endpoints under `/remote/v1/schedule` (PUT push, GET list/show, DELETE remove, GET logs); five matching `aegis_schedule_*` MCP tools (push/list/show/remove/logs, each with optional `target=` for cross-host); CLI `aegis schedule push --to <peer>` and `--remote <peer>` flag on inspection verbs. Pushed schedules land in the receiver's `.aegis/schedules/<name>.yaml` overlay folder with a `# pushed_from:` provenance comment; the v0.6 hot-reload watcher picks them up and they become indistinguishable from native schedules. Source classification (`inline` / `overlay` / `pushed`) is surfaced in list + show responses.

Spec: `docs/superpowers/specs/2026-05-25-aegis-remote-callbacks-schedule-control-design.md`.

## [0.7.1] - 2026-05-25

### Changed
- **Remote-plane public surface rewritten** to drop the
  Telegram-as-default-return-channel framing that crept in from the
  design spec. The remote plane has no built-in return channel; the
  `callback_note` string returned to the calling agent now reads
  *"no wire return channel in v1; completion behavior is whatever
  the receiving serve is configured to do"*. README, docs/remote.md,
  docs/index.md, docs/roadmap.md, docs/configuration.md, and the
  `aegis_enqueue` docstring rewritten in the same voice. Example
  URLs are now neutral tailnet IPs.
- No code-behavior changes — only one user-visible string (the
  `callback_note`) and the `aegis_enqueue` docstring. The wire
  protocol, queue semantics, and config schema are unchanged from
  0.7.0.

## [0.7.0] - 2026-05-25

### Added
- **Remote plane.** Server-to-server enqueue over HTTP. `aegis serve`
  exposes a second HTTP plane (distinct from the loopback MCP plane),
  bound to whatever address you want it reachable from, that other
  `aegis serve` instances can POST into. `aegis_enqueue` grows an
  optional `target=` parameter that routes the call to a configured
  remote's `/remote/v1/enqueue`; the remote enqueues into its own
  `QueueManager` (recorded with `enqueued_by="remote:<from>"`) and
  runs the worker on its own filesystem under its own agent profiles.
  In v1 there is **no wire return channel** — completion behavior is
  whatever the receiving serve is configured to do on queue
  completion; the calling aegis is not notified over the wire. Two
  new top-level sections in `.aegis.yaml`: `remotes` (outbound peers;
  `url` plus optional `token`; per-name overlay files at
  `.aegis/remotes/<name>.yaml` with fail-loud collision detection)
  and `remote_plane` (inbound bind address + optional
  `accept_tokens` bearer allowlist + optional `accept_from`
  source-IP allowlist; gates compose with AND; default off). All
  failure paths return clear, distinguishable error dicts to the
  calling agent — no silent fallback to local enqueue. Recommended
  deployment binds the plane to a private overlay network (Tailscale,
  Headscale, WireGuard, VPN) so the network itself acts as the
  outermost trust boundary; tokens and IP allowlists are
  defense-in-depth knobs on top. Docs: `docs/remote.md`.

## [0.6.0] - 2026-05-25

### Added
- **Agent groups.** Sixth coordination primitive: named committees
  of agents with one in-flight broadcast slot, a four-field broadcast
  contract (`objective`, `output_format`, `tool_guidance`,
  `boundaries`), `wait_all` and `wait_any` waiters (the latter with
  passive loser cancellation via `group:<name>/cancel:<id>` inbox
  envelopes), four built-in reducers (`concat`, `join_by_handle`,
  `last_wins`, `majority_vote`) plus `register_reducer` for custom
  reductions, append-only JSONL audit per group under
  `.aegis/state/groups/<name>.jsonl` with on-boot replay that ignores
  torn trailing lines. Nine MCP tools (`aegis_group_spawn`,
  `aegis_group_spawn_mixed`, `aegis_group_broadcast`,
  `aegis_group_wait_all`, `aegis_group_wait_any`, `aegis_group_status`,
  `aegis_group_dissolve`, `aegis_group_rename`,
  `aegis_group_move_member`). Mirror surface on `WorkflowEngine`
  (`spawn_group` / `broadcast` / `wait_all` / `wait_any` /
  `dissolve_group` / `rename_group` / `move_member`) plus the
  `engine.ephemeral_group(profiles=[…])` context manager for
  one-shot committees. YAML configuration: `groups:` section in
  `.aegis.yaml` with `defaults:` and `presets:`, drop-in overlays at
  `.aegis/groups/<name>.yaml`, preset-name collisions fail loud.
  `aegis_group_spawn_mixed(preset=...)` resolves presets from
  config. TUI surface: `GroupTabState` with aggregate-state emoji
  (`✓` / `⏳` / `⚠` / `⛔`) and `GroupDashboard` render with three
  panels (Members, Current broadcast, Recent broadcasts).
- **Scheduler substrate.** Cron-style scheduled workflow execution
  inside `aegis serve`. Declarative in `.aegis.yaml` under a top-level
  `schedules:` section; drop-in overlays under `.aegis/schedules/<name>.yaml`
  merge into the table with fail-loud conflict detection. Each entry
  declares `workflow`, `args`, a trigger (`cron` or `fire_at`), a
  `lifecycle` (`forever`, `once`, `{fires: N}`, `{until: <iso>}`),
  `on_overlap` (`skip` / `queue` / `kill`), and optional `notify` /
  `timeout` / `enabled` knobs. A single asyncio tick loop walks the
  table every 60 s, dispatches eligible entries through the workflow
  runner, and appends lifecycle events (`fire_requested` /
  `fire_completed` / `fire_failed`) to `.aegis/state/schedules/<name>.jsonl`.
  A derived snapshot at `.aegis/state/schedules.snapshot.json` carries
  the next-fire-time + in-flight flag per schedule for dashboards.
  On-boot replay rebuilds `fire_count` from the JSONL, closes dangling
  `fire_requested` records as `failed:interrupted`, and flags
  past-due fires for a single backfill.
- **Built-in workflows.** `prompt(agent, text)` spawns an agent, sends
  one message, closes; `enqueue(queue, payload, callback=false)` is
  the canonical scheduler→queue handoff.
- **`aegis schedule` CLI.** `list / show / run / enable / disable / logs`.
  `enable` / `disable` go through a comment-preserving ruamel.yaml
  editor so operator-curated YAML survives automation.
- **Hot reload.** A watchdog observer over `.aegis.yaml` and the
  overlay folders re-reads the config on every edit and atomic-swaps
  the running scheduler's schedule table. Parse errors keep the prior
  config intact and append a `reload_failed` record to
  `.aegis/state/aegis_events.jsonl`.

Spec: `docs/superpowers/specs/2026-05-25-aegis-scheduler-design.md`.

## [0.5.1] - 2026-05-23

### Fixed
- `tests/test_cli.py::test_version_flag_prints_and_exits` and
  `tests/test_cli_clean_flag.py::test_clean_flag_shows_in_help` both
  failed on CI for the v0.5.0 tag (the former hard-coded the prior
  version string; the latter assumed no ANSI escapes in Typer/Rich
  help output, which CI runners trigger via `FORCE_COLOR=1`).
  v0.5.0 was tagged but never published to PyPI as a result — 0.5.1
  is the first release of the 0.5.x line.

## [0.5.0] - 2026-05-23

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
