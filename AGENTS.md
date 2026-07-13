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
                                  # (agent / queue / default-agent
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

`aegis` and `aegis serve` both resolve the project root via
`find_project_root()` (closest ancestor containing `.aegis.yaml`); the
harness subprocess is rooted there unless `--cwd` overrides.
`.aegis.yaml` is the single config substrate — it carries `agents:`,
`queues:`, `schedules:`, `remotes:`, `groups:`, `web:`, and
`plugin_dirs:` sections. Drop-in overlays live under
`.aegis/{agents,queues,schedules,groups}/*.yaml` and merge fail-loud
with inline entries. `@workflow`-decorated functions are registered by
auto-importing every `*.py` under each `plugin_dirs` entry (default
`.aegis/plugins/`).

## Package management

Use `uv` (not pip): `uv pip install -e .`, `uv run pytest`.

## Layout

- `src/aegis/cli.py` - typer entrypoint (`aegis`, `aegis serve`,
  `aegis web`, `aegis workflow`, `aegis budget`, `aegis schedule`,
  `aegis models`, `aegis plugin`)
- `src/aegis/cli_config.py` - the `aegis config ...` subapp; all writing
  verbs route through `aegis.config.edit` helpers.
- `src/aegis/tui/config_panel.py` - the TUI ConfigPanel tab + AddAgentModal;
  mounted at boot when there's no `.aegis.yaml`, also reachable mid-session
  via `F2`.
- `src/aegis/config/__init__.py` - Agent / Permission / Effort /
  Provider dataclasses + `find_project_root`, `load_config`,
  `load_queues`, `load_telegram_config` — all YAML-backed thin
  wrappers around `aegis.config.yaml_loader.load_config`.
- `src/aegis/config/yaml_loader.py` - the real YAML parser:
  `.aegis.yaml` + overlays → `AegisConfig` (agents, queues, schedules,
  remotes, groups, telegram, plugin_dirs). Fail-loud on default_agent /
  queue-agent / max_parallel violations.
- `src/aegis/drivers/` - HarnessDriver seam + concrete drivers.
  `claude.py` (Claude Code, full-featured — multi-turn via stream-json INPUT,
  per-invocation MCP injection via `--mcp-config`). `acp.py` is the generic
  `AcpDriver`/`AcpSession` on the official `agent-client-protocol` SDK;
  `gemini.py`, `opencode.py`, and `lovelaice.py` are thin `BASE_CMD` shims over
  it (`gemini --acp`, `opencode acp`, `lovelaice-acp`). Registry is `DRIVERS`
  in `drivers/__init__.py`, keyed by harness string.
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
- `src/aegis/core/` - harness-agnostic session core: `AgentSession`
  (turn loop, metrics, state, observer callbacks — `session.py`) and
  `SessionManager` (AppBridge impl: spawn/close/interrupt/handoff over
  many AgentSessions — `manager.py`). The TUI's ConversationPane and the
  web frontend both delegate to these.
- `src/aegis/tui/` - Textual app shell (app.py) + per-tab ConversationPane
  (pane.py), TabBar/StatusBar (widgets.py), AgentState (state.py),
  SessionMetrics (metrics.py), generated handles (names.py), AgentPicker
  modal (picker.py), PendingStrip/Chip — the click-to-dequeue queue of
  text-box messages shown above the input while the agent is mid-turn
  (pending.py), Theme registry + AegisColors role map (themes.py;
  `aegis-ink` default). `pane.py` also renders the subagent view: a
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
  callback=True) and `aegis_task_status`. `aegis_handoff` now flows
  through the same inbox channel — target agents read handoffs and
  callbacks through one consistent surface (universal tagging).
  Queues are declared in `.aegis.yaml` under `queues:` as
  `<name>: {agent: <profile>, max_parallel: N}`; unknown agent
  references fail loud at `aegis serve` boot.
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

The `live` marker is registered in `pyproject.toml`; do not use
`-k "not live"` — it matches `live` as a substring and silently eats
unrelated names (e.g. anything containing `deliver`).

On zion the full suite intermittently flakes 1–2 TUI/watchdog tests
(inotify watch limit) — non-deterministic, and they pass in isolation.
Prefer `uv run python -m pytest`; when a TUI test fails in a full run,
re-run it alone (`uv run python -m pytest <path>::<test> -v`) before
treating it as a real failure. Gate on a blast-radius subset, not the
full suite, during iteration.

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
