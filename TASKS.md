# Aegis — Tasks / Next

Working roadmap for what's next. Shipped history lives in `CHANGELOG.md`;
the public roadmap is `docs/roadmap.md`. This file is the scratch /
priority list — keep it terse and current.

Current release: **v0.11.2** (file indexer + picker UX, 2026-05-26).

## Time-sensitive (June 2026 billing changes)

### ⚠️ Before June 15 — Claude driver: `claude -p` → REPL mode

Anthropic splits interactive vs programmatic billing on June 15. `claude -p`
(current driver) hits the new metered credit pool (full API rates). Interactive
REPL stays on the subscription bucket unchanged.

Change: strip `-p` from spawn argv; write prompts to `proc.stdin` instead of
passing as a CLI argument. Output stream-JSON protocol is identical. The VS Code
Claude extension already works this way.

- Spec: `docs/superpowers/specs/2026-05-27-aegis-claude-repl-driver-design.md`
- Plan: `docs/superpowers/plans/2026-05-27-aegis-claude-repl-driver-v1.md` *(armed, not yet executed — no `drivers/claude_repl.py` on disk)*
- Roadmap context: `vault/Atlas/Architecture/2026-05-25-aegis-harness-roadmap.md`

### ⚠️ Before June 18 — `GEMINI_API_KEY` support in Gemini agent profile

Gemini CLI's personal OAuth dies June 18 for Google AI Pro/Ultra accounts.
Fix: add optional `api_key` field to `GeminiCLI` profile config; inject
`GEMINI_API_KEY=<value>` into the subprocess env at spawn time. User gets an
API key from Google AI Studio (free tier available) and puts it in `.aegis.py`.
No driver changes, no ACP changes — the subprocess just picks up the env var.

### After June 1 billing transition — Copilot ACP driver

GitHub Copilot CLI supports ACP since Jan 2026: `copilot --acp` (stdio).
Driver is a four-line `AcpDriver` shim — same shape as `GeminiDriver`.
Auth goes through `gh auth login` (no separate token management).

## Active

### Native lovelaice agent (harness-free) *(VS1–VS5 shipped — on main + PyPI)*

aegis ships `lovelaice` as a dependency and drives `lovelaice-acp` over official
ACP v1 — a native agent that runs local or direct-API models with no external
harness. Shipped across lovelaice 2.7.0→2.11.0: v1 ACP server (legacy `AcpServer`
frozen for warden), per-session MCP attach (calls the aegis plane), full toolset
(read/bash/write/edit/glob/list_dir), token usage, streaming, `load_session`
resume, and cancel. aegis side: `Lovelaice` provider + `LovelaiceDriver` +
`extra_env`/`session_id`/`interrupt` on the generic ACP driver.

- Docs: `know-how/native-lovelaice-agent.md`; spec
  `docs/superpowers/specs/2026-07-10-lovelaice-native-acp-agent-design.md`;
  plans `docs/superpowers/plans/2026-07-{10,13}-lovelaice-native-agent-*`.
- **Deferred (own slice):** `workflow/run` + `conversation/archive` as ACP
  ext-methods — no consumer until warden migrates off the legacy dialect.
- Open: human eyeball of the native-agent render in a real TUI tab.

### Web client S1–S8 *(shipped — browser-verified, on main)*

Full web frontend of `aegis serve`: single-tab → multi-tab (picker, switching,
unseen markers, title pulse, cross-window coherence), Alt-based keyboard,
markdown/native-HTML/source file viewer (Alt+P), queue dashboard (Alt+Q),
group dashboard (Alt+G), config panel with editing (F2), theme switcher with
localStorage (Alt+Y). All slices specced + TDD'd + live-smoked in Chrome.

- WS protocol: `docs/superpowers/specs/2026-06-30-aegis-web-ws-protocol-design.md`
- Design + slice plans: `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`
  and `docs/superpowers/plans/2026-06-30-aegis-web-client-s*.md`
- Omnigent comparison that seeded the priority:
  `docs/superpowers/specs/2026-06-30-omnigent-vs-aegis-adoption-report.md`

### ⏭️ Web client S9–S10 — TUI becomes a WS client *(next; spec ready, not planned)*

The architectural unification: run the Textual TUI as a WS client of
`aegis serve` (via a `RemoteSessionManager` implementing `AppBridge` over the
protocol S1–S8 built), so TUI + web + Telegram share one backend and sessions
are visible across all three. Opt-in `--remote` → flip default with `--classic`
fallback. Touches the daily-driver TUI + moves the MCP plane into serve, so it
wants a fresh, deliberate pass. Prerequisite: extend the WS protocol to cover
the auxiliary surfaces the TUI drives (handoff, rename, group *ops*, terminals,
canvas, workflow) — staged surface-by-surface, each step shippable.

- Spec: `docs/superpowers/specs/2026-07-01-aegis-tui-ws-client-design.md`
- Slice breakdown (S9.0–S10) in the spec; start with S9.0 (conversation-loop
  protocol parity: add `handoff` + `rename_handle` RPCs) then S9.1 `ws_client.py`.

### Plugin substrate v1 *(complete — all 5 slices shipped in 0.15.0)*

Five-slice plan landed end-to-end on 2026-05-28: hooks (`@hook` — `pre_turn`
mutator + `post_turn` / `session_start` / `session_end` observers), tools
(`@tool` decorator + FastMCP registration with reserved-name guard), plugin
lifecycle (`plugin.toml` manifest, `InstallContext`, local-path install with
rollback, lockfile, `_install.py` / `_uninstall.py` hooks), registry
resolution (`gh:owner/repo#path` + `file://`, `git archive` HTTPS fetch,
`aegis plugin install / uninstall / list / show / update / search`), and the
canonical `plugins/skill-system/` plugin (pre_turn skill injection +
`load_skill` MCP tool, live-tested against a real `claude` subprocess).

- Spec: `docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-plugin-substrate-v1.md`
- Release notes: `CHANGELOG.md` § 0.15.0

Deferred to follow-ups (per spec § "Deferred — call-outs for future work"):
Tier B hook events (`pre_tool_use`, `post_tool_use`, `on_error`, `on_interrupt`,
`on_handoff`, `on_enqueue`); per-agent-profile tool scoping
(`agents.<name>.tools: [...]`); plugin-version constraints + inter-plugin
deps; Tier B substrate-events bus. Revisit when a concrete plugin demands
one.

### memory-system plugin *(shipped — v0.1.0)*

Second canonical plugin: Hermes-inspired persistent memory with
periodic dreaming. Exercises every v1 substrate primitive (`@hook`,
`@tool`, `@workflow`) end-to-end.

- Spec: `docs/superpowers/specs/2026-05-30-aegis-memory-plugin-design.md`
- Plan: `docs/superpowers/plans/2026-05-30-aegis-memory-plugin-v1.md`
- Release notes: `CHANGELOG.md` § memory-system plugin (v0.1.0)

### Driver visibility parity *(complete — all 7 slices shipped)*

Make every tool call legible across drivers: semantic kind icon, path hint,
structured input retained, success/failure styling. Slice 1 shipped
(`3f6772b` → `763e1b6`) — `ToolUse` / `ToolResult` carry `kind`, `tool_call_id`,
`raw_input`, `locations`, `status`; `_AegisAcpClient` and the claude parser
populate them; `render_event` shows a glyph per kind (📖 ✏️ ⌬ 🔎 ✻ 🌐 ➡️ 🗑 🔄 ⏺)
and a path-tail hint; codec round-trips through `state/event_codec.py` with
legacy-record decode. Two ride-along bug fixes: ACP `is_error` now derives from
`status=="failed"`, Gemini usage falls back to `field_meta.quota.token_count`.

Slice 2 shipped (`f141b51` → `de1fd68`): `AssistantText` / `AssistantThinking`
carry `message_id` from both drivers; new pure helper
`aegis.render.coalesce_chunks` merges adjacent same-`(type, message_id)`
chunks; `replay_blocks` pipes through it before rendering. Smoke against
real `opencode acp`: 80 raw events → 9 coalesced; opencode's per-token ✻
cliff is gone. Live pane streaming was already kind-coalesced via
`_stream_append`; this slice closes the same gap on the replay path.

Slice 3 shipped (`2648551` → `81b4956`): canonical `AgentPlan` event +
`PlanEntry` dataclass; claude parser promotes `TodoWrite` tool_use to
`AgentPlan`; ACP `AgentPlanUpdate` notification maps to the same event;
renderer shows a `📋 Plan — N/M done` block with status glyphs
(● completed, ◐ in_progress, ○ pending) and priority emphasis. Real-CLI
smoke against an opencode planning turn surfaced 4 distinct plan
revisions live (0/3 → 1/3 → 2/3 → 3/3). Polish item deferred: replace
prior `AgentPlan` from same turn instead of appending — ship if it
becomes noisy in real use.

Slice 4 shipped (`b1cd895` → `28e25a4`): `ToolResult.diff` field
carries `(path, old_text, new_text)`. ACP driver extracts it from
`FileEditToolCallContent` in `ToolCallProgress.content`. Claude parser
synthesizes from the matching `Edit`/`Write` tool_use input via the
new `ParserState.tool_diffs` cache. Renderer shows a small unified
preview — capped at 6 visible rows with truncation footer — with `-`
red and `+` green gutters. Real opencode write of a 5-line file
surfaces the full added content live in the transcript.

Slice 5 shipped (`8f9965c` → `dae8963`): `Result` carries stop_reason,
ttft_ms, num_turns, cost_usd, model_usage, permission_denials. Both
drivers populate (ACP cost comes from the last mid-turn UsageUpdate;
Gemini's per-model attribution from field_meta.quota.model_usage).
Renderer's terminator line surfaces cost + non-default stop_reason
when fired. Codec backward-compatible.

Slice 6 shipped (`72d7fc5` → `247b154`): canonical `ContextUpdate` +
`CostUsage`; ACP `session_update` maps UsageUpdate / CurrentModeUpdate /
SessionInfoUpdate to the canonical event. Renderer returns None
(transcript stays clean); status-bar / metrics consumption is a polish
follow-on.

Slice 7 shipped (`7840def`): `SystemInit` carries model, permission_mode,
version, available_commands. Claude reads from `system.init`; ACP emits
at boot from `InitializeResponse.agent_info` and follows with a second
`SystemInit` carrying available_commands when
`AvailableCommandsUpdate` fires.

The 7-slice arc is complete. The canonical event surface now exposes
every signal both substrates publish. Polish follow-ons (status-bar
consumption of `ContextUpdate`, plan-block replacement-within-turn,
TTFT for ACP) remain candidate work but aren't on the critical path.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-driver-visibility-parity-design.md`
- Slice-1 plan: `docs/superpowers/plans/2026-05-28-aegis-driver-visibility-slice1.md` *(status: shipped)*

### Session history (`Ctrl+H`)

Modal listing every user-initiated agent session (open or closed, current
process or previous); reopens via jump-to-tab, `drv.resume()`, or fresh spawn
with recorded profile + cwd. Three slices: backend reads → resume path with
`session_id` latch → close marker + preview + Telegram parity.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-session-history-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-session-history.md`

### Aegis filesystem tool surface

Six aegis-owned tools (`aegis_bash`, `aegis_read`, `aegis_write`, `aegis_edit`,
`aegis_grep`, `aegis_listdir`) routing every agent's filesystem + shell access
through the substrate. `PermissionRouter` (`allow` / `deny` / `ask`) with TUI
inline + Telegram inline-button approval. Hard Claude built-in suppression via
`--tools ""`. Universal "prefer aegis tools" system-prompt addendum.

- Spec: `docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md`
- Plan: `docs/superpowers/plans/2026-05-27-aegis-fs-tool-surface-v1.md`

### Agent sandbox *(designed, no plan yet)*

Per-profile opt-in isolation primitives: worktree isolation, declarative
read-only / hidden filesystem partitioning, outbound network block. Backend:
`bubblewrap` for filesystem + network (Linux-only); native `git worktree add`
for worktrees.

- Spec: `docs/superpowers/specs/2026-05-27-agent-sandbox-design.md`
- Plan: *not yet drafted*

### Queue v1 polish

Small follow-ups on top of the shipped substrate:

- **Worker tab handle suffix** (T4.1 deferred) — `<handle> · <queue>#<task>`
  in the TUI tab bar so workers are visible at a glance. Touches
  `tui/widgets.py`, `tui/app.py`, `tui/pane.py`.
- **`aegis_cancel(task_id)` MCP tool** — cancellation currently flows through
  `aegis_handoff` to the worker's inbox; a dedicated tool would be cleaner.
- **`aegis_delegate` sync wrapper** — single MCP call that does enqueue + await
  internally for callers that want the simple sync shape. Composes on the
  existing primitives.
- **Telegram delivery sanity test** (T4.3 deferred) — verify the substrate
  header survives chunking and reaches the Telegram chat.

### Sequential handoff — re-scope

Original framing (vision Phase 4): agent A summarises its current task state
and retires; agent B (potentially a different harness) is instantiated and
continues from where A left off.

Adjacent work has since shipped (workflow `send/drain/caller_handle`, inbox
arrivals with a visible block, canvas substrate, agent groups, remote plane).
Worth re-scoping before picking up — figure out what's left vs what's already
in the substrate.

### OpenAI Codex JSON-RPC driver

Codex CLI exposes a bidirectional JSON-RPC app server (`codex exec --json`).
Different from ACP but documented and stable. Needs a custom `CodexDriver`
implementing `HarnessSession` over JSON-RPC. Auth: `OPENAI_API_KEY` env var.
No deadline pressure.

### Web client + TUI WS-client migration *(designed, no plan yet)*

First-class web frontend (desktop), feature parity with the TUI. Hybrid
visual idiom (TUI-faithful transcript via `render_event_html`, native-web
chrome via HTMX + Jinja). One multiplexed WS per browser window; subscribe
sends full session history then live events; reconnect via `(session_id,
last_seq)` resume against the existing JSONL persistence. Themes move to
shared YAML (`src/aegis/data/themes/*.yaml`) so TUI and web stay visually
identical. End-state: TUI also becomes a WS client of `aegis serve` so
sessions are shared across TUI ↔ web ↔ Telegram.

Ten slices, S1–S10, vertical, foundation-first. Earliest "usable single-tab
web client" is end of S2; full TUI feature parity is end of S6; full
architectural unification (TUI flipped to `--remote` default with `--classic`
fallback) is S10. Each slice is an honest stop point.

- Spec: `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`
- Plan: *not yet drafted — start with S1 (theme YAML + shared render refactor)*

## Backlog

### Antigravity CLI (after June 18)

Google's closed-source replacement for Gemini CLI. Probe for ACP support after
it ships (`agy --help | grep acp`). If ACP confirmed: three-line shim identical
to `GeminiDriver`. If not: probe stream-JSON and write a parser.

## Watching

- **VPS job-crawler dispatched the plan job (2026-05-20-aegis-task-queue-plan)
  but never picked up its follow-up implement job** (file existed on VPS
  with `status: armed` and `fire_at` in the past, crawler was healthy
  and firing every 60s). One-off so far; needs a closer look at the
  crawler's eligibility logic if it happens again. Filing here, not
  acting on it yet.
