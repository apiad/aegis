# Agents

## Running

    aegis                         # full-screen TUI — first-class UI for
                                  # local dev (opens ConfigPanel when
                                  # there's no .aegis.yaml)
    aegis web                     # installable PWA — first-class UI for
                                  # remote (and local) dev; ensures a token,
                                  # opens the browser, serves the web client
    aegis serve                   # headless: MCP plane + web frontend
    aegis config ...              # scriptable .aegis.yaml authoring
                                  # (agent / harness / queue / default-agent
                                  #  / plugin-dir / show)

The TUI and the web/PWA client are **two co-equal first-class UIs** over
one `aegis serve` backend: the TUI for local dev, the web client for
remote dev over a flaky link (mobile-first, installable) and local dev
too. Both render the same transcripts with the same fidelity.

## Know-how

Procedure docs under `know-how/` — match the task, load the doc before acting:

- `know-how/deploying-web.md` — *reach for it when deploying / redeploying /
  debugging the public aegis web UI (`dev.apiad.net`) on the VPS.*
- `know-how/native-lovelaice-agent.md` — *reach for it when working on the
  native (harness-free) `lovelaice` agent / driver: config, MCP injection,
  streaming, resume, cancel, and the real-model-probe discipline.*
- `know-how/remote-tui.md` — *reach for it when running the TUI against a
  remote or auto-launched aegis serve (via `--remote ws://…` or
  `ssh://…`), or debugging the WS client / SSH tunnel path.*
- `know-how/releasing.md` — *reach for it when cutting a release / bumping
  the version / pushing a `vX.Y.Z` tag — especially the `uv.lock` re-lock
  gate that has failed the PyPI publish twice.*
- `know-how/ssh-execution-hosts.md` — *reach for it when configuring or
  debugging a `hosts:` entry — a session whose harness runs on another
  machine (`/spawn main@vps`) — or the SSH ControlMaster / reverse MCP
  tunnel behind it. Distinct from `--remote` and from `remotes:`.*

`aegis` and `aegis serve` both resolve the project root via
`find_project_root()` (closest ancestor containing `.aegis.yaml`); the
harness subprocess is rooted there unless `--cwd` overrides.
`.aegis.yaml` is the single config substrate — it carries `agents:`,
`queues:`, `schedules:`, `remotes:`, `hosts:`, `groups:`, `web:`, and
`plugin_dirs:` sections. Drop-in overlays live under
`.aegis/{agents,queues,schedules,hosts,groups}/*.yaml` and merge fail-loud
with inline entries. `@workflow`-decorated functions are registered by
auto-importing every `*.py` under each `plugin_dirs` entry (default
`.aegis/plugins/`).

## Package management

Use `uv` (not pip): `uv pip install -e .`, `uv run pytest`.

## Layout

- `src/aegis/cli.py` - typer entrypoint (`aegis`, `aegis serve`,
  `aegis web`, `aegis workflow`, `aegis budget`, `aegis schedule`,
  `aegis models`, `aegis usage`, `aegis plugin`)
- `src/aegis/cli_config.py` - the `aegis config ...` subapp; all writing
  verbs route through `aegis.config.edit` helpers.
- `src/aegis/tui/config_panel.py` - the TUI ConfigPanel tab + AddAgentModal;
  mounted at boot when there's no `.aegis.yaml`, also reachable mid-session
  via `F2`.
- `src/aegis/config/__init__.py` - Agent / Permission / Effort /
  Provider dataclasses + `find_project_root`, `load_config`,
  `load_queues` — all YAML-backed thin
  wrappers around `aegis.config.yaml_loader.load_config`. `Agent` also
  carries an optional `prompt:` (persona system-prompt file path).
- `src/aegis/config/harnesses.py` - the `harnesses:` registry:
  `HarnessRegistration` (named provider entry = driver + credentials/
  endpoint), `IMPLICIT_HARNESSES` (the four driver strings auto-register
  so legacy `provider:` configs keep working), `merge_harnesses`, and
  `resolve_agent_entry` (maps an agent's `harness:` ref → driver string +
  credentials; legacy `provider:` shape builds the provider directly).
  Agents pick a harness + model + effort; the same driver can be
  registered twice with different endpoints. `config/persona.py`
  (`read_persona`) reads the persona file at spawn.
- `src/aegis/config/yaml_loader.py` - the real YAML parser:
  `.aegis.yaml` + overlays → `AegisConfig` (agents, queues, schedules,
  remotes, groups, plugin_dirs). Fail-loud on default_agent /
  queue-agent / max_parallel violations.
- `src/aegis/drivers/` - HarnessDriver seam + concrete drivers.
  `claude.py` (Claude Code, full-featured — multi-turn via stream-json INPUT,
  per-invocation MCP injection via `--mcp-config`). `acp.py` is the generic
  `AcpDriver`/`AcpSession` on the official `agent-client-protocol` SDK;
  `gemini.py`, `opencode.py`, and `lovelaice.py` are thin `BASE_CMD` shims over
  it (`gemini --acp`, `opencode acp`, `lovelaice-acp`). Registry is `DRIVERS`
  in `drivers/__init__.py`, keyed by driver string.
  **OpenCode model selection**: `opencode acp` has no `-m` flag, so
  `OpenCodeDriver.extra_env` injects `OPENCODE_CONFIG_CONTENT={"model":…}`
  (live-verified; free `opencode/…-free` models work). **Personas**: claude
  appends a 2nd `--append-system-prompt` after the primer; ACP drivers
  prepend the persona as a leading text block on the first turn (native
  seams deferred). Per-session `model`/`effort`/`prompt` overrides thread
  through `SessionManager.spawn` / `aegis_spawn` / `/spawn` via
  `core/manager._overlay_agent` (never persisted); the TUI `AgentPicker` is
  two-tier (presets + harness→model→effort custom path).
  **`lovelaice.py` is the native, harness-free agent** — it spawns `lovelaice-acp`
  (lovelaice's ACP v1 server, a dependency of aegis) and runs local or direct-API
  models with no external CLI. Model / `base_url` / API key are injected as env at
  spawn via the `AcpDriver.extra_env(agent)` seam (from the `Lovelaice` provider's
  `model` / `base_url` / `api_key_file`). Because ACP `new_session` carries
  `mcp_servers`, the lovelaice agent gets **per-session** aegis-MCP injection
  (can call `aegis_enqueue` / `aegis_claim` / …) — unlike Gemini/OpenCode workers,
  whose MCP config is global, so they run their task but can't call back. Also:
  `AcpSession.session_id` (needed for `resume()`), `AcpSession.interrupt()` sends
  ACP `session/cancel`, and lovelaice streams deltas + supports `load_session`
  resume. Per-provider config classes (`ClaudeCode`, `GeminiCLI`, `OpenCode`,
  `Lovelaice`) in `config/__init__.py` carry only the fields each provider uses;
  legacy flat `Agent(harness="…", model=…, …)` still works via a back-compat shim.
  See `know-how/native-lovelaice-agent.md`.
- `src/aegis/events.py` - stream-json parser (typed events). Events carry
  `parent_tool_use_id` (set only by the claude parser, from claude's stream
  field) — the grouping key for the subagent view. ACP-built events leave it
  `None`, so ACP renders flat.
- `src/aegis/render.py` - pure render_event(ev) -> Rich renderable | None
- `src/aegis/commands/` - slash commands: aegis-executed control commands
  typed into the input box (never sent to the harness), a second front-end
  over the `AppBridge` parallel to the MCP plane. `args.py` (declarative
  `ArgSpec`/`Args` parser), `__init__.py` (registry + `dispatch()` +
  `classify_input()` + `CommandResult.effect`), `builtins/` (one module per
  family: `core` = help/sessions/agents/spawn/queues/enqueue,
  `coordination` = groups/schedules, `terminals`, `session_ctl` =
  rename/close/themes/clear). Harness-agnostic; the TUI (`tui/pane.py`) and
  web (`web/wssession.py` + `app.js`) seams both call `dispatch()` and apply
  any `effect`. Palette (2D): `fuzzy.py` (scorer) + `complete(text, bridge)`
  (introspects the registry + `Arg.completer` seam) drive the TUI drop-up
  `tui/palette.py` (`CommandPalette` + `GrowingInput.key_interceptor`) and the
  web `complete` RPC. Specs/plans: `docs/superpowers/{specs,plans}/*slash-commands*`.
- `src/aegis/core/` - harness-agnostic session core: `AgentSession`
  (turn loop, metrics, state, observer callbacks — `session.py`) and
  `SessionManager` (AppBridge impl: spawn/close/interrupt/handoff over
  many AgentSessions — `manager.py`). The TUI's ConversationPane and the
  web frontend both delegate to these.
- `src/aegis/state/` - conversation persistence. `session_log.py` owns the
  transcripts at `.aegis/state/sessions/<log_id>.jsonl`. **The log id is
  `<YYYYMMDDTHHMMSSuuuuuuZ>-<birth handle>`, minted once at spawn
  (`new_log_id`) and never changed** — do not key a log on a handle:
  handles come from a finite pool, `generate_name` only avoids *live*
  ones, and reusing one merges unrelated conversations into one file (100
  of 223 logs on a real state dir, 160 conversations buried). A rename
  therefore moves nothing; it appends a `SessionMeta` carrying the new
  name. A bare handle stays a valid (legacy) id, so pre-id files keep
  resolving. Writes are one `os.write` on an `O_APPEND` fd + `fsync` on
  turn barriers; reads (`scan_log`) skip damaged lines and salvage whole
  records embedded in them via the `{"v":1,"aegis_ts":"` resync marker,
  never raising — a damaged transcript must never take a session, let
  alone the app, down. `history.py` folds logs into Ctrl+R rows (headers
  found anywhere, rows rebuilt for header-less legacy logs and flagged
  `inferred`); `workspace.py` is the atomic tab roster; `repair.py` backs
  `aegis doctor [--repair] [--split]`.
- `src/aegis/tui/` - Textual app shell (app.py) + per-tab ConversationPane
  (pane.py), TabBar/StatusBar (widgets.py), AgentState (state.py),
  SessionMetrics (metrics.py), generated handles (names.py), AgentPicker
  modal (picker.py), PendingStrip/Chip — the click-to-dequeue queue of
  text-box messages shown above the input while the agent is mid-turn
  (pending.py), Theme registry + AegisColors role map (themes.py;
  `aegis-ink` default), and the `F3` **Sidebar** (sidebar.py) — the
  dashboard column holding SESSION / CONTEXT / PLAN / QUEUES / MONITORS /
  REPOS / SYSTEM, ordered by volatility because the panel scrolls. `F3` toggles a
  *mode*, and the mode is **app-wide**: `AegisApp.sidebar_mode` is the one
  flag, `set_sidebar_mode` fans it out to every pane, and a pane adopts it
  in `on_mount` — so `F3`, `/tasks` and `pane.toggle_task_dock()` all move
  every tab, and a tab opened later comes up in the mode rather than
  collapsed beside its siblings. Per pane, one `-sidebar` class hides
  `QueueStrip`,
  `MonitorStrip`, `PlanStrip` and `StatusBar` by CSS, and the main column
  becomes transcript + input. **A collapsed surface must therefore never
  set `display` imperatively** — an inline style beats the rule, which is
  why `PlanStrip` uses the `-empty` class its siblings already used.
  Two more the column has already paid for: **`SIDEBAR_MIN/MAX` are the
  frame, so they carry `SIDEBAR_PAD_X` on top of the content budget** —
  charging the rows for the padding drops a segment outright at 80 cols,
  because `fit_rows` answers "no tier fits" by omitting it, so a *section
  disappears* rather than a row getting shorter; and **a section composes
  a renderer without inheriting its framing** — `render_plan_dock` is
  free-standing, so `_plan()` trims its `tasks d/t` header and trailing
  newline at the composition site rather than in the renderer, which
  keeps its own contract in `tests/test_plan_render.py`.
  `pane.py` also renders the subagent view: a
  `Task`/`Agent` tool_use opens a collapsible `SubagentBox`; events tagged
  with that `parent_tool_use_id` route inside (the web mirrors this in
  `coalesce.js`/`renderEvent.js`). tool_use↔tool_result pairing folds by
  `tool_call_id` (works for all drivers).
- `src/aegis/mcp/` - FastMCP server (`server.py`: BRIEFING/PRIMING,
  `aegis_meta` + slice-2 inter-agent tools `aegis_list_sessions`,
  `aegis_list_agents`, `aegis_handoff`, `aegis_spawn` (genuine
  fire-and-forget peer spawn — new top-level agent + opening prompt +
  `spawned_by` provenance) + claims tools `aegis_claim` /
  `aegis_release` / `aegis_claims` (inter-agent file-claims registry)
  + queue-v1 tools `aegis_enqueue`,
  `aegis_task_status`; `mcp_config_json`) + `AppBridge`/`SessionInfo`
  (`bridge.py`: pure Protocol the server consumes; `AegisApp` and
  `SessionManager` both implement it) + `AegisMCP` runtime
  (`runtime.py`: co-resident HTTP server, port pick, start/stop,
  `bind(bridge)`). The app owns one shared instance, binds itself,
  starts it before the first spawn, and injects strict
  (`--mcp-config` + `--strict-mcp-config`) into every spawned claude
  alongside a primer system-prompt that bakes the pane's handle
  (`PRIMING.format(handle=…)`). Each agent reads its own handle from
  its system prompt and passes it as `from_handle` to
  `aegis_handoff` / `aegis_enqueue`. aegis sessions run
  `--strict-mcp-config`: the user's other MCP servers are not present
  inside aegis; built-in claude tools (Read/Edit/Bash/…) are unchanged.
- `src/aegis/queue/` - inter-agent task queues + agent inboxes.
  **A turn boundary is not completion.** Ending a turn is how an agent
  *waits* — the monitor briefing says "returns {monitor_id} immediately;
  END YOUR TURN" — so `_finalize` consults
  `close_guard.still_working_reasons` before it marks a task done, sends
  the callback, or closes the worker; if the worker is waiting it logs a
  `deferred` record and returns, and the next turn boundary tries again.
  Reading the boundary as completion once closed a worker mid-wait,
  orphaning its monitor and stranding real work uncommitted in a shared
  checkout (2026-08-10, `repos/ainbox` warden). Two rules inside it: only
  **self-terminating** conditions defer (monitors time out, reminders
  fire, inbox messages resolve next turn) — a held **file claim does
  not**, because nothing but the holder releases it and the slot would
  be pinned forever; and the facts come from the shared
  `close_guard.gather_facts`, never a second copy, because
  `aegis_close` has refused exactly this since it shipped. Note the
  finalizer's `except` around the probe **logs** rather than swallows:
  a bare one hid an `AttributeError` from `gather_facts` and made the
  fix read as inert against its own failing tests.
  `QueueManager` (FIFO + max-parallel cap + substrate-deterministic
  dispatch on every enqueue/completion event; JSONL lifecycle log
  under `.aegis/state/queues/<queue>.jsonl`; `start()` replays on
  boot and marks in-flight tasks `failed:interrupted`),
  `InboxRouter` (per-handle delivery; wake-on-idle / mid-turn buffer /
  turn-end chain through `AgentSession.deliver`, which returns a
  `Delivery(landed|queued, depth)` receipt; JSONL writethrough
  under `.aegis/state/inboxes/<handle>.jsonl`), schema records
  (`Queue`, `Task`, `InboxMessage`, `Delivery`) + helpers (`new_ulid`,
  `now_iso`, `sender_agent`/`sender_queue`/`sender_user`,
  `render_inbox_header`). Text-box input is delivered as a headerless
  `sender_user` message (plain user turn); `AgentSession.cancel_pending`
  drops a still-buffered message by identity (chip dequeue); the
  `on_dispatch` observer fires when a buffered batch starts its turn.
  MCP surface: `aegis_enqueue` (queue, payload, from_handle,
  callback=True), `aegis_task_status`, `aegis_cancel` (drop-if-pending /
  interrupt+close-if-in-flight, idempotent), and `aegis_delegate`
  (synchronous enqueue+await — returns the worker's result directly,
  optional `timeout_s`, no inbox callback). `QueueManager.worker_label`
  suffixes in-flight worker tabs with `<queue>#<task>`. `aegis_handoff`
  flows through the same inbox channel — target agents read handoffs and
  callbacks through one consistent surface (universal tagging).
  Queues are declared in `.aegis.yaml` under `queues:` as
  `<name>: {agent: <profile>, max_parallel: N}`; unknown agent
  references fail loud at `aegis serve` boot.
- `src/aegis/peer/` - `@peer`: asking an **idle** peer from where you are
  standing. `PeerAnswer` (+`footer`), `refusal` (the guard — reads the
  *target*, never the source, so `@peer` is legal while your own pane is
  mid-turn), `teaser` (a 2k-token window of the source transcript, a log
  read and **no model call** — that is what lets the design push a pointer
  and let the peer pull the rest), `compose` (provenance of *place, not
  author*: tagged `agent:<handle>` a peer reads it as delegation and skews
  autonomous), `send_and_await`, `read_window` (backs `aegis_read_peer`),
  `cc_into`, and `ask` — the half both `AppBridge` implementations share,
  the same split `btw.side_note_for` uses. `@handle …` is sugar for
  `/peer handle …`, rewritten in `classify_input`, so no new input route
  exists. Spec:
  `docs/superpowers/specs/2026-07-31-aegis-at-mention-peer-ask-design.md`.
  `compose_spawn` is the same push aimed at a *new* agent, used by
  `/spawn <agent> <prompt>` through
  `commands.builtins.core._spawn_opening`: same provenance-of-place +
  tail + `aegis_read_peer` pull, with the ending inverted (a peer is told
  not to start long work; a spawn is told to do it, and to hand off
  back). Two rules already paid for: the preamble **rides on the tail**,
  so every failure path returns the bare prompt rather than pointing a
  fresh agent at a transcript nobody can read; and it asks `read_peer`
  for the **TEASER** budget, not the READ default — measured on a real
  410KB transcript, the READ budget made a 3-turn window 95,346 chars,
  because one long in-flight turn is a single turn and the turn bound
  never binds. That is why `read_peer` takes `budget_tokens` /
  `item_chars` on **both** bridges
  (`test_read_peer_takes_the_same_window_knobs_on_both_bridges`): the
  caller swallows exceptions, so a signature that drifted on one bridge
  would drop the preamble in that frontend and nowhere else. Spec:
  `docs/superpowers/specs/2026-08-10-aegis-spawn-with-provenance-design.md`.
- `src/aegis/plan/` - agent plan state as first-class session state, in two
  layers. The **parser** (`events.py`) folds the `TaskCreate`/`TaskUpdate`
  delta family and `TodoWrite` into the one cumulative `AgentPlan` event, so
  claude-legacy, claude-current and ACP all arrive in one shape. The
  **tracker** (`tracker.py`, owned by `AgentSession`, routed by
  `parent_tool_use_id` so a subagent gets its own) adds per-task working
  time; `models.py` holds `PlanTask`/`PlanState`/`PlanSnapshot` and
  `render.py` the pure strip and dock renderers. The strip
  (`tui/plan_strip.py`) is the collapsed surface; `render_plan_dock` now
  draws the PLAN section of the `F3` sidebar (`tui/sidebar.py`, also
  `/tasks`) rather than a dock of its own, and `SessionInfo.plan` +
  `aegis_peer_plan` are readers of the same tracker.
  Four rules a contributor will otherwise break, each already paid for:
  **circles are always space-separated** (East Asian Ambiguous — Rich
  measures one cell, terminals draw wider, neighbours overlap);
  **the tracker never reads a clock** — every method takes an explicit
  `ts`, which is what makes a replayed log reproduce the live numbers, and
  `rehydrate_plan` depends on it to restore a plan across a restart;
  **width budgets are measured in cells, not `len()`** (one emoji is one
  character and two columns, and the row's clock drifts out of its column);
  and a dock row is `glyph + space + label + space + a 6-cell clock`, so
  **the label budget is `width - 9`** while the widget's `size` is already
  the content box and must not have its padding subtracted again.
- `src/aegis/repos/` - which git repos the live agents are writing to,
  backing the `REPOS` section of the `F3` sidebar. `writes.py`
  (`write_target` — Claude's write tools by name, ACP's by *kind*, since
  every ACP harness titles its tools differently; Bash deliberately
  excluded); `probe.py` (`find_repo_root` walks up for `.git`, which yields
  the *nearest* root and so resolves `repos/aegis` inside a git-tracked
  workspace to `aegis`; `probe_repo` folds one
  `git status --porcelain=v2 --branch` into branch/ahead/behind/dirty plus
  an in-progress-operation flag read off disk, and degrades to a `stale`
  branch-only state on any failure rather than raising into a paint);
  `tracker.py` (`RepoTracker` — app-owned, keyed `(host, root)`,
  `record`/`drop`/`rename`/`snapshot`/`subscribe`, TTL-gated `refresh`
  running probes in the executor); `render.py` (pure rows).
  Three rules a contributor will otherwise break, each already paid for:
  **the narrowest row tier truncates rather than being dropped** —
  `fit_rows` omits a segment whose narrowest tier overflows, so a long repo
  name makes the *row vanish*, which reads exactly like a repo nobody
  touched; **the recording hook is `AgentSession._fire_event`, which the
  replay walk does not call** — move it somewhere replay reaches and a
  resumed session repopulates the board from its whole transcript; and
  **an off-host path is never resolved against the local disk** — the same
  string names a different tree there, so `git status` locally returns a
  silently wrong answer rather than an error (same reasoning as
  `Claim.host` and `render_shared.file_target`). The probe runs only while
  the sidebar is open. Spec:
  `docs/superpowers/specs/2026-08-10-aegis-sidebar-repos-section-design.md`.
- `src/aegis/workflow/` - the workflow scaffold (v1). `@workflow`
  decorator + auto-registry (`decorator.py`); `WorkflowEngine` runtime
  with `delegate` (one-shot via queue), `send`/`drain` (live-agent
  fire-and-forget + await idle), `spawn`/`close` (long-lived agent
  lifecycle), `bash` (async shell), `log` (stderr + JSONL under
  `.aegis/state/workflows/`), and `caller_handle` (whoever invoked
  via MCP `aegis_run_workflow`); `runner.run_workflow` is the unified
  entry for CLI (`aegis workflow run`) and MCP (`aegis_run_workflow`),
  with auto-drain + auto-close in finally. Compose on the v1 queue
  for delegation; no second agent-spawn plane.
- `src/aegis/scheduler/` - cron-style scheduled workflow execution.
  `clock.py` (SystemClock + FakeClock); `cron.py` (croniter +
  zoneinfo, UTC-normalized `next_fire`); `lifecycle.py` (`is_exhausted`
  predicate for `forever` / `once` / `{fires: N}` / `{until: <iso>}`);
  `scheduler.py` (single-asyncio tick loop, JSONL audit under
  `.aegis/state/schedules/<name>.jsonl`, atomic `schedules.snapshot.json`,
  `replace_schedules` for hot reload, `fire_now` for manual dispatch,
  `on_overlap: skip|queue|kill`); `replay.py` (boot replay rebuilds
  fire_count + closes dangling `fire_requested` as `failed:interrupted`);
  `notify.py` (`Notifier` + `maybe_notify` hook); `reload.py`
  (`ReloadWatcher` — watchdog Observer + async debounced reload,
  exceptions swallowed and logged). Built-in workflows in
  `src/aegis/workflows/{prompt,enqueue}.py` register on import.
  `src/aegis/cli_schedule.py` mounts the `aegis schedule` subapp;
  `src/aegis/config/edit.py` does comment-preserving YAML edits via
  ruamel + atomic tempfile rename.
- `src/aegis/groups/` - agent-group substrate (sixth coordination
  primitive). `models.py` (`Group`, `MemberRef`, `MemberResult`,
  `GroupResult`, `BroadcastRecord`); `registry.py` (in-memory map +
  in-flight broadcast tracker; emits persistence events on every
  mutation; auto-dissolves a group that drops to zero members);
  `runtime.py` (`broadcast` / `wait_all` / `wait_any`, the last with
  passive loser cancel via `group:<name>/cancel:<id>` inbox tags);
  `reducers.py` (`concat`, `join_by_handle`, `last_wins`,
  `majority_vote` + `register_reducer`); `persistence.py` (per-group
  append-only JSONL log at `.aegis/state/groups/<name>.jsonl`,
  torn-trailing-line tolerant, replays on boot); `wiring.py`
  (`spawn_many` / `spawn_group` sugars); `bridge.py` (`_GroupsBridge`
  surface). MCP surface: nine `aegis_group_*` tools. Mirror methods
  on `WorkflowEngine` + `engine.ephemeral_group()` context manager.
  YAML config at `.aegis.yaml` `groups:` + overlays under
  `.aegis/groups/<name>.yaml`; `aegis_group_spawn_mixed(preset=...)`
  resolves named presets.
- `src/aegis/locks/` - inter-agent file-claims registry (seventh
  coordination primitive; supersedes `bin/ws-lock` for aegis agents).
  `models.py` (`Claim` + `claims_overlap` — prefix-containment ∪
  set-intersection); `resolver.py` (`resolve_paths` — prefixes/files/glob
  split, globs resolved to concrete paths at claim time); `registry.py`
  (`ClaimRegistry` — grant rule, `release`, `reap`, `start` boot-replay,
  and a `live_handles` filter that drops a dead session's claims);
  `persistence.py` (JSONL log + boot replay under `.aegis/state/locks/`);
  `bridge.py` (`_LocksBridge` + `make_locks_bridge`). Intents: `shared`
  ("I'm working here, FYI" — shared∩shared coexist) vs `exclusive`
  ("keep out" — denied on any overlap); a denied claim is not recorded.
  Claims auto-reap on session close (the live-handle filter). MCP surface:
  `aegis_claim` / `aegis_release` / `aegis_claims`. New store, coexists
  with `bin/ws-lock`; per-host v1.
- `src/aegis/hosts/` - SSH execution hosts (eighth coordination
  primitive): running a harness process on another machine while the
  session, transcript and MCP peer identity stay local. `models.py`
  (`HostSpec` config entry + `Place` resolved host+cwd); `resolve.py`
  (`resolve_place` precedence — explicit > profile default > local — and
  `parse_at_host` for `agent@host:/cwd`); `launcher.py` (the `Launcher`
  seam both driver families spawn through: `LocalLauncher` is today's
  `create_subprocess_exec`, `SshLauncher` wraps the same argv into
  `ssh -T <dest> 'cd <cwd> && exec <argv>'`); `connection.py`
  (`HostConnection` — one ControlMaster per host, `-R 0` reverse tunnel
  carrying the local MCP port with the allocated port parsed off ssh's
  stderr, preflight, teardown); `registry.py` (`HostRegistry` — one
  connection per host; `launcher_for` is SYNCHRONOUS because
  `_sync_spawn` is, so the connection opens lazily inside
  `SshLauncher.spawn`, which also puts connection errors in a pane that
  exists to show them).
  Host is a **third orthogonal spawn axis** beside agent profile and
  harness — any harness on any host, resolved per spawn and never
  persisted, exactly like the model/effort overrides.
  Two traps worth knowing: **`login_shell` defaults to True** because a
  non-interactive ssh command never sources the profile, so a harness in
  `~/.local/bin` is not on `PATH` and every spawn dies at preflight; and
  **only one reader per pipe** — `SshLauncher.watch_stderr()` is opt-in
  and called only by `ClaudeSession`, since `AcpSession` drains its own
  stderr.
  Paths are host-scoped: `Claim.host` gates overlap, and `file_target`
  returns `None` off-host so ctrl+click cannot silently open the
  identically-named local file. NOT the same as `--remote` (TUI attached
  to a remote serve) or `remotes:` (federated serves) — see
  `know-how/ssh-execution-hosts.md`.
- `src/aegis/tui/groups/` - TUI surface for groups. `state.py`
  (`GroupTabState` + aggregate-state emoji); `dashboard.py`
  (`GroupDashboard` widget with `render_dashboard` pure function —
  Members / Current broadcast / Recent broadcasts panels).
- `examples/` - shipped workflows (`tdd_step.py`). Drop them into
  `.aegis/plugins/` (or any `plugin_dirs:` entry in `.aegis.yaml`) to
  register them.
- Theme colors are threaded as an `AegisColors` object (`app.palette`,
  passed into `render_event`/`dot`/widgets) — not a module global; the
  app attribute is `palette` (not `colors`) to avoid shadowing Textual's
  `App.colors`
- `docs/superpowers/{specs,plans}/*.md` - specs & plans are **Markdown**
  (canonical, source of truth). A handful of older Phase-2 docs remain as
  `.html`; Markdown is the default for all new specs/plans and the only
  format that propagates across hosts.

## Tests

`uv run pytest -q -m "not live"` for the fast hermetic suite. Drop the marker
filter to include the live round-trip tests against the real CLI subprocesses
— each auto-skips when the corresponding CLI is off PATH:
- `tests/test_integration_live.py`, `tests/test_mcp_live.py`, and
  `tests/test_queue_live.py`, `tests/test_workflow_live.py` need `claude`.
- `tests/test_drivers_multiprovider_live.py` exercises `gemini` and
  `opencode` driver round-trips (each subtest skips independently).
- `tests/test_lovelaice_mcp_live.py` and `tests/test_lovelaice_resume_live.py`
  drive a real `lovelaice-acp` subprocess against a real model, so they need
  `lovelaice-acp` on PATH *and* the OpenRouter token — and they fail, rather
  than skip, when the model endpoint is having a bad day.

The `live` marker is registered in `pyproject.toml`; do not use
`-k "not live"` — it matches `live` as a substring and silently eats
unrelated names (e.g. anything containing `deliver`).

**A failing test is a real failure.** The suite used to flake 1–2
TUI/watchdog tests per full run on zion, and the standing advice was to
re-run them alone before believing them. That is no longer true, and
following it now would mask regressions. The flakes had three causes,
all fixed in 0.25.0:

- the file indexer released its watchdog observer only in `action_quit`,
  so every test app leaked one of the user's 128 inotify instances until
  `Observer.start()` began raising `EMFILE` partway through a run;
- two teardown races panicked the app — the compositor rendering a
  pruned `TextArea`, and message handlers reaching for a
  `ContentSwitcher` that teardown had already removed;
- every test shared the repo's own `.aegis/state`, so one test's saved
  workspace was resumed by the next test's app (and by the next run — a
  leftover terminal entry alone could hang an unrelated test). Tests now
  get a per-test project dir from the autouse `isolated_project_dir`
  fixture in `tests/conftest.py`; anything resolving state from
  `Path.cwd()` inherits the isolation.

Prefer `uv run python -m pytest`. Gate on a blast-radius subset during
iteration, but treat a red full run as a regression to investigate, not
noise to re-roll.

Regenerate parser fixtures with `scripts/capture_fixtures.sh` (captures real
`claude` stream-json output, then sanitizes identifiers/paths before commit).

Regenerate `src/aegis/data/models.yaml` (model registry + prices) with
`scripts/refresh-models.py` — pulls from `https://models.dev/api.json` (the
catalog OpenCode itself consults). Run manually: `--diff` to preview, `--apply`
to write. Update the script's curation lists when adding a new provider or
when a model rev requires the canonical-key name to change.

After pushing a new `models.yaml` to `main`, installed aegis instances pick
it up within 24h via the background fetch into `~/.cache/aegis/models.yaml`.
To force the local cache to refresh immediately:

    aegis models refresh       # synchronous fetch + reload
    aegis models clear         # delete cache, fall back to bundled
    aegis models list [prov]   # show what aegis currently sees

## Conventions

- TDD: failing test first, then minimal implementation, commit per logical unit.
- `claude -p` with `--output-format stream-json` also requires `--verbose` and
  `--input-format stream-json --replay-user-messages` — see
  `drivers/claude.py:build_argv`.
- The TUI is Textual 8.x. Interrupt is `Escape` (Textual reserves `ctrl+c`).
  The line REPL was removed in Phase 1.5; there is no `--plain` mode, so the
  TUI requires a TTY. Live/driver tests do not go through the App.
- Tab order *is* `AegisApp._panes` order — the ContentSwitcher keys on pane
  id, not on child order — so reordering (`Ctrl+Shift+←→`, or a mouse drag
  emitting `TabBar.Reordered`) is one list splice in `_move_pane` plus a
  `_refresh_tabbar()`. The drag takes no mouse capture: cells are positional,
  so the cell under the pointer already is the drop target and Textual's
  routing does the hit-testing; the gate is `MouseMove.button` (1 while a
  drag is in flight, 0 on a plain hover).
- Block gestures (`CopyableBlock.on_click`): plain click copies (or expands a
  tool call's args); `ctrl+click` opens a file *here* — from the block's
  `FileTarget` when it has one (Read/Write/Edit, computed by
  `render_shared.file_target`), else by resolving a backtick token through the
  picker; `alt+click` (`meta`) hands the token to `xdg-open`. An Edit's line
  comes from `render_shared.anchor_line` at click time, not render time — the
  edit has already removed its own `old_string` from the file.
- Input gestures (`GrowingInput`): `Enter` enqueues (chips mid-turn);
  `Alt+Enter` / `Ctrl+Enter` send-with-interrupt (cut the live turn, send now;
  `Alt+Enter` is the portable key, `Ctrl+Enter` needs the Kitty protocol);
  `Shift+Enter` / `Ctrl+J` insert a newline; `Esc` clears a non-empty input,
  else interrupts the turn; `Up`/`Down` recall sent-message history
  (boundary-aware — only at the first/last line — per-pane, session-lifetime,
  draft-preserving). The input outline echoes the state dot: vivid `$success`
  when idle, subdued `$foreground 30%` while working (`.working` class toggled
  in `_on_core_state`), amber `$warning` while voice-recording.
- `aegis_handoff(from_handle, target_handle, context, interrupt=False)`:
  `interrupt=True` cuts a busy peer's current turn before delivering (via
  `AppBridge.interrupt(handle)`) so the handoff lands as the peer's next turn.

## Plugins

The plugin substrate (`src/aegis/plugins/`, `src/aegis/hooks/`,
`src/aegis/tools/`) lets users extend aegis without forking it. Three
primitive shapes:

- `@workflow` (existing) — user/agent/scheduler-invoked orchestration.
- `@hook("<event>")` — fires on harness lifecycle events. Tier A in v1:
  `pre_turn` (mutator), `post_turn`, `session_start`, `session_end`.
  See `src/aegis/hooks/contexts.py` for payload shapes.
- `@tool` — first-class MCP tool the agent can call. Auto-schema from
  type hints + docstring via FastMCP.

Plugins live under `.aegis/plugins/<name>/` and are auto-imported on
session start (full recursion; `_*.py` and `_*` directories skipped).
The aegis repo's own `plugins/` folder is the default registry served
at `gh:apiad/aegis#plugins/`.

CLI: `aegis plugin {install, uninstall, update, list, search, show}`.

The canonical `skill-system` plugin replicates Claude Code's
skill-selection behavior on any harness. See
`plugins/skill-system/` and the design spec at
`docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md`.

## Python

Requires Python 3.13+.
