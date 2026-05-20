# Agents

## Running

    aegis init && aegis           # full-screen TUI
    aegis serve                   # headless: MCP plane + optional Telegram

`aegis` and `aegis serve` both resolve the project root via
`find_project_root()` (closest ancestor containing `.aegis.py`); the harness
subprocess is rooted there unless `--cwd` overrides.

## Package management

Use `uv` (not pip): `uv pip install -e .`, `uv run pytest`.

## Layout

- `src/aegis/cli.py` - typer entrypoint (`aegis`, `aegis init`)
- `src/aegis/config.py` - Agent profile + .aegis.py loader
- `src/aegis/drivers/` - HarnessDriver seam; ClaudeDriver in claude.py
- `src/aegis/events.py` - stream-json parser (typed events)
- `src/aegis/render.py` - pure render_event(ev) -> Rich renderable | None
- `src/aegis/core/` - harness-agnostic session core: `AgentSession`
  (turn loop, metrics, state, observer callbacks — `session.py`) and
  `SessionManager` (AppBridge impl: spawn/close/interrupt/handoff over
  many AgentSessions — `manager.py`). The TUI's ConversationPane and the
  Telegram frontend both delegate to these.
- `src/aegis/telegram/` - Telegram bot front-end: `BotClient` (long-poll
  Bot API with exponential backoff + `retry_after` handling — `bot.py`),
  pure formatting helpers (`format.py`: `escape_md`, `status_line`,
  `chunk`), and `TelegramFrontend` (`/new /close /interrupt /agents
  /sessions /<handle> /help`, bare-text routing with auto_prompt suffix,
  mid-turn status refresher — `frontend.py`). Activated by `aegis serve`
  when `telegram_token` + `telegram_chat_id` are configured in
  `.aegis.py` (token also accepted via `AEGIS_TELEGRAM_TOKEN`).
- `src/aegis/tui/` - Textual app shell (app.py) + per-tab ConversationPane
  (pane.py), TabBar/StatusBar (widgets.py), AgentState (state.py),
  SessionMetrics (metrics.py), generated handles (names.py), AgentPicker
  modal (picker.py), Theme registry + AegisColors role map (themes.py;
  `aegis-ink` default)
- `src/aegis/mcp/` - FastMCP server (`server.py`: BRIEFING/PRIMING,
  `aegis_meta` + slice-2 inter-agent tools `aegis_list_sessions`,
  `aegis_list_agents`, `aegis_handoff` + queue-v1 tools `aegis_enqueue`,
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
  turn-end chain through `AgentSession.deliver`; JSONL writethrough
  under `.aegis/state/inboxes/<handle>.jsonl`), schema records
  (`Queue`, `Task`, `InboxMessage`) + helpers (`new_ulid`,
  `now_iso`, `sender_agent`/`sender_queue`, `render_inbox_header`).
  MCP surface: `aegis_enqueue` (queue, payload, from_handle,
  callback=True) and `aegis_task_status`. `aegis_handoff` now flows
  through the same inbox channel — target agents read handoffs and
  callbacks through one consistent surface (universal tagging).
  Queues are declared in `.aegis.py` as
  `queues = {"<name>": {"agent": "<profile>", "max_parallel": N}, …}`;
  unknown agent references fail loud at `aegis serve` boot.
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
- `examples/` - shipped workflows (`tdd_step.py`). Import in your
  `.aegis.py` to register them.
- Theme colors are threaded as an `AegisColors` object (`app.palette`,
  passed into `render_event`/`dot`/widgets) — not a module global; the
  app attribute is `palette` (not `colors`) to avoid shadowing Textual's
  `App.colors`
- `legacy/` - sidelined workflow-engine prototype (not built, not tested)
- `docs/superpowers/{specs,plans}/*.html` - specs & plans are self-contained
  HTML (house format), not Markdown

## Tests

`uv run pytest -q -m "not live"` for the fast hermetic suite. Drop the marker
filter to include the live `claude` round-trip (`tests/test_integration_live.py`,
`tests/test_mcp_live.py`; auto-skip if `claude` is not on PATH). The `live`
marker is registered in `pyproject.toml`; do not use `-k "not live"` — it
matches `live` as a substring and silently eats unrelated names (e.g.
anything containing `deliver`).

Regenerate parser fixtures with `scripts/capture_fixtures.sh` (captures real
`claude` stream-json output, then sanitizes identifiers/paths before commit).

## Conventions

- TDD: failing test first, then minimal implementation, commit per logical unit.
- `claude -p` with `--output-format stream-json` also requires `--verbose` and
  `--input-format stream-json --replay-user-messages` — see
  `drivers/claude.py:build_argv`.
- The TUI is Textual 8.x. Interrupt is `Escape` (Textual reserves `ctrl+c`).
  The line REPL was removed in Phase 1.5; there is no `--plain` mode, so the
  TUI requires a TTY. Live/driver tests do not go through the App.

## Python

Requires Python 3.13+.
