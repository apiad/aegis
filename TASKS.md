# Aegis — Tasks / Next

Working roadmap for what's next. Shipped history lives in `CHANGELOG.md`;
the public roadmap is `docs/roadmap.md`. This file is the scratch /
priority list — keep it terse and current.

Current release: **v0.28.1** (2026-07-29).

## Resolved — the June 2026 billing scare

Both ⚠️ deadlines below have passed and neither action is needed. Kept as a
record so nobody re-arms them off the old spec files.

### ~~Before June 15 — Claude driver: `claude -p` → REPL mode~~ — MOOT

**Anthropic cancelled the billing split before it took effect.** `claude -p`
and Agent SDK usage both still draw from the Pro/Max subscription pool; there
is no separate credit to migrate away from. Never executed — there is no
`drivers/claude_repl.py` on disk, and there should not be.

- Superseded by `docs/superpowers/specs/2026-07-30-aegis-claude-agent-sdk-driver-design.md`,
  which reworks the Claude driver for capability rather than billing.
- Dead spec/plan: `docs/superpowers/{specs,plans}/2026-05-27-aegis-claude-repl-driver-*`

### ~~Before June 18 — `GEMINI_API_KEY` in the Gemini agent profile~~ — OVERTAKEN

Never implemented (no `api_key` on `GeminiCLI` in `config/__init__.py`), and
overtaken by events: Gemini CLI subscription access died 2026-06-18 and the
`gemini --acp` driver is dead weight for subscription users. See *Backlog →
Subscription-backed models*, which supersedes this. Do the API-key field only
if someone actually wants to pay Google AI Studio rates.

### After June 1 billing transition — Copilot ACP driver

GitHub Copilot CLI supports ACP since Jan 2026: `copilot --acp` (stdio).
Driver is a four-line `AcpDriver` shim — same shape as `GeminiDriver`.
Auth goes through `gh auth login` (no separate token management).

## Active

### Claude driver on the Agent SDK *(specced 2026-07-30, no plan yet)*

A **second** Claude driver (`claude-sdk`) beside `claude-code`, built on
`claude-agent-sdk` (PyPI 0.2.128) instead of a hand-built argv plus the
stream-json parser in `events.py`. The SDK spawns the *same* local `claude`
binary over the *same* protocol — this is not a billing or transport change.

The case is that three specced-and-unbuilt items are options on
`ClaudeAgentOptions`: `can_use_tool` is the `PermissionRouter` from the
fs-tool-surface spec (with `updated_input`, it also rewrites calls, which that
spec doesn't); `ClaudeSDKClient.set_model()` retires the `/model` half of 2B.1
with no resume-restart; `session_id` + `fork_session` collapse claude's id space
into the aegis log id (`9da13d0`) and hand us conversation fork for free. Plus
`hooks`, `max_budget_usd`, `agents`, `skills`, `setting_sources`.

Prior art: `pingdotgg/t3code` does exactly this in TypeScript
(`apps/server/src/provider/Layers/ClaudeAdapter.ts`).

- Spec: `docs/superpowers/specs/2026-07-30-aegis-claude-agent-sdk-driver-design.md`
- Plan: *not yet drafted — start with slice 1 (walking skeleton) and let its
  `parent_tool_use_id` probe decide the shape of slice 2*
- Known costs, all called out in the spec: the canonical-event mapping is the
  real work (`parent_tool_use_id` and `ParserState.tool_diffs` are the two easy
  things to drop); `HarnessDriver.build_argv` is abstract and argv-shaped, so
  demote it to a non-abstract `return []`; **`pre_spawn` hooks stop applying** —
  the main reason this ships alongside rather than replacing; `system_prompt`
  has one `append` slot where `build_argv` passes two (primer, then persona, in
  that order — pin it with a test).
- Registration trap: four places, and the fourth is
  **`_VALID_DRIVERS` at `config/yaml_loader.py:73`** — missing it is the exact
  lovelaice bug fixed in `449ebbb`. Gate on a YAML-declared agent that spawns,
  not a unit test of the driver class.

### Conversation fork *(specced 2026-07-30, no plan yet)*

A forked agent inherits the parent's **entire conversation** and continues
under a new handle. This retires the workaround baked into the MCP briefing —
*"the worker is a fresh agent with no context — write the payload as a
self-contained prompt with everything it needs"* — which is the largest cost
of delegating anything today.

**Not gated on `claude-sdk`** — `claude --fork-session` is a real flag on the
CLI aegis already drives (probed 2026-07-30), so `ClaudeDriver.fork()` is the
same three-line argv insertion as `.resume()`. `claude-sdk` inherits it via
`fork_session=True`. `opencode run --fork` exists too, but aegis drives
`opencode acp` and ACP v1 has no fork verb.

`fork()` is a sibling of
`resume()` on `HarnessDriver` with a `supports_fork` flag; MCP verb
`aegis_fork` is shaped like `aegis_spawn` (fire-and-forget, provenance via a
new `forked_from`). Per-fork `model`/`effort` overrides come free from the
existing `_overlay_agent`. Forking a *closed* session works too — it needs a
`session_id`, not a live process — so `Ctrl+R` gains fork-beside-reopen.

- Spec: `docs/superpowers/specs/2026-07-30-aegis-conversation-fork-design.md`
- Plan: *not yet drafted — VS1 is the driver seam + `aegis_fork` + three
  refusals*
- **The cost is the open question.** A fork's first turn pays the whole
  parent conversation as input tokens; N forks pay it N times. Caching should
  soften it to cache-read rates — unmeasured. VS1 logs the number, and group
  fan-out is deferred until it exists.
- Deferred to their own specs: group fan-out (`GroupRuntime.wait_all` needs an
  open broadcast, and `broadcast()` sends one objective to all members — no
  per-member angle, so fan-out needs a design decision); worktree isolation
  (worktree the *repo*, never the project root — `repos/` is gitignored and
  `vault/` must not be branched under autosync).

### Session titles + one-shot generation seam *(specced 2026-07-30, no plan yet)*

Ten tabs read `lucid-knuth` / `deep-dijkstra`. 0.28 made many tabs fast; it
didn't make them legible. CLAUDE.md's *"rename yourself once the purpose has
settled"* is the workaround — and it overloads a rename, which is **identity**
(`from_handle`, inbox routing, half the log id), with a job labels should do.

So: a `title` beside the handle, never instead of it. Handle semantics are
untouched. `title_source ∈ {auto, agent, human}` with strict `human > agent >
auto` precedence — which is also the whole concurrency story, since a late
auto-generation simply can't overwrite a human title. Stored by appending a
`SessionMeta` (already the mutation record — a rename appends one), so
`Ctrl+R` rows get readable for free.

Surfaces: `/title <text>` (human), `aegis_title` (agent), and an optional
`title=` on the existing `aegis_rename` so an agent can self-name in one call.

Second half is a **one-shot structured-generation seam on `HarnessDriver`**
(`supports_oneshot` + `generate(schema, *instructions)`) — no session, no MCP,
no tools. All four drivers can do it (verified on zion): claude
`-p --output-format json --json-schema`, gemini `-p -o json`, opencode
`run --format json`, lovelaice `lingo.Engine.create` (already in the venv via
`lovelaice>=2.11`). `text_generation: <profile>` in `.aegis.yaml` keeps a title
from costing Opus tokens. The seam generalises to generated commit subjects and
branch names later, which is why it's `generate(schema, …)` not
`generate_title()`.

- Spec: `docs/superpowers/specs/2026-07-30-aegis-session-titles-design.md`
- Plan: *not yet drafted — slice 1 (storage + manual set) ships value with
  zero LLM calls; generation is slices 2–4*
- Traps: gemini/opencode have no `--json-schema`, so a shared tolerant parser
  is needed; `lingo.Engine.create` returns `parsed=None` on reasoning models
  that emit JSON into the reasoning channel (observed on LM Studio) — fall back
  to `LLM.chat` + the same parse.
- Open: whether the tab bar has room at all — the cell already renders dot,
  index, handle, slug, and a muted suffix (which `QueueManager.worker_label`
  owns on worker tabs). Might belong in `Ctrl+R` + status bar instead. A
  screenshot answers this, not a spec.

### ✅ Dynamic workflows — Track 2 JSON DSL *(all 6 slices shipped — v0.19.0)*

Shipped end-to-end: `src/aegis/dsl/` (`models`/`interpreter`/`refs`/`validate`/
`plan`/`gate`), `aegis_run_dynamic_workflow` MCP tool, per-node checkpoint/
resume durability, bounded `map`/`parallel`/`loop`/`if` + `shell`/`judge`
predicates, `human` node via `ask_human`, and the cost gate
(`dynamic_workflow_autoapprove_agents`) — the first landing of the Track-1
gating rule too. 59 DSL tests green. **Deferred follow-on:** wire the same
cost-gate into the existing `aegis_run_workflow` (Track-1 Python) path.

Agent-authorable dynamic workflows as a validated JSON DSL — the safe/data
counterpart to Track-1 durable `@workflow` Python. Premise: harnesses now own
intra-harness fan-out (Claude Dynamic Workflows, Codex/Gemini subagents), so
aegis workflows reposition to what a single harness structurally can't do —
cross-harness, cross-restart/host durability, and mid-run human-in-the-loop.

Key decisions (see spec): the interpreter is itself a `@workflow` (inherits
durability/resume/gating); control flow = static shapes + hard-bounded
`loop`/`if` with typed `shell`|`judge` predicates; data flow =
select-never-compute selectors; leaf `target` = `spawn`|`session`|`queue`;
`human` node (TUI-only); gating = operator-implicit / agent-prompt-above-a-cost-
threshold. Also lands the missing **Track-1 gating** (operator implicit / agent
prompts, showing the script).

- Spec: `docs/superpowers/specs/2026-07-17-aegis-json-dsl-dynamic-workflows-design.md`
- Open questions (explicit decision points, not resolved in v1): `equals`
  predicate (avoid a `judge` agent-call when branching on a known value — felt
  at the design-thinking gate; `AnyPredicate` union left extensible); plan-preview
  cost estimate is a labelled static upper bound, not a prediction.
- Plan: `docs/superpowers/plans/2026-07-17-aegis-json-dsl-dynamic-workflows.md`
  — 6 vertical slices, thinnest-first, TDD per slice:
  1. walking skeleton (`sequence` + `agent`/`spawn`, `dynamic` @workflow);
  2. data-flow (`refs` selectors/templates, agent `inputs`/`schema`, semantic
     `validate`) + per-node checkpoint/resume durability;
  3. fan-out (`map` bounded concurrency + `parallel` barrier);
  4. bounded control flow (`loop`/`if` with `shell`+`judge` predicates,
     decision replay);
  5. `human` node (TUI via `ask_human`);
  6. `aegis_run_dynamic_workflow` MCP tool + `plan.py` preview + cost gate +
     the missing Track-1 gating rule + config threshold key.
- Grounding caveats flagged in the plan: no gating machinery exists to inherit
  (built from scratch in slice 6); durability rides `engine.checkpoint`/
  `resume_state` (not a bespoke ledger); `engine.parallel` has no concurrency
  cap (interpreter adds a `Semaphore`); shell predicate uses `engine.bash`
  (not `bash_predicate`); structured output is prompt-engineered + parsed;
  `jsonschema` promoted from transitive to direct dependency.

### Slash commands — Phase 2 *(decomposed into 2A–2D)*

Phase 1 shipped (v0.17.0): control commands `/help /sessions /agents /spawn
/queue /enqueue` + `!` shell escape, harness-agnostic registry + pure
`dispatch()`, magenta/blue input accents. Spec:
`docs/superpowers/specs/2026-07-16-aegis-slash-commands-design.md`.

Phase 2 (the powerful system) is decomposed into sub-specs, each its own
spec → plan → implement cycle; web parity threaded through each:

- [x] **2A — parser + resolution core** *(shipped)* — declarative typed args
  (`ArgSpec`/`Args`, flags-anywhere, greedy-verbatim), protected-builtin
  resolution with `source` tags + `CommandCollision`, `//` literal-slash
  escape (`classify_input`), `/queue new` persistence (`--ephemeral` opts
  out), and web-input parity (`deliver` routes slash → `command_result`).
  Spec: `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2a.md`.
- [x] **2B — full builtin coverage** *(shipped)* — operator-useful subset
  over the `AppBridge`: `/groups`, `/schedules`, `/terminals`, `/rename`,
  `/close`, `/themes`, `/clear`, plus agent management folded into `/agents`
  and queue listing on `/queues` (renamed from `/queue`). Collection nouns are
  plural and a bare noun-command lists. Adds the `CommandResult.effect`
  channel (frontend-applied theme/clear) and a `list_groups` bridge method.
  `/handoff` dropped (redundant with tab-switch for the operator; agent→agent
  stays MCP); `/config` dropped (agent verbs live on `/agents`). Spec:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2b.md`.
- [ ] **2B.1 — session-mutation slice** — `/model`, `/effort` via
  resume-restart (mutate the live `Agent`, tear down + `resume()` with the new
  argv so the conversation survives). Deferred from 2B: driver-capability-
  dependent session surgery that warrants its own spec + TDD pass.
  **Splits once `claude-sdk` lands** (see *Claude driver on the Agent SDK*):
  `ClaudeSDKClient.set_model()` does `/model` live on that driver with nothing
  to tear down. `/effort` does not split — there is no `set_effort()`, so it
  still needs the resume-restart, on every driver.
- [x] **2C — prompt commands + plugin `@command`** *(shipped)* — user-authored
  `.aegis/commands/<name>.md` (frontmatter `description`/`argument-hint` +
  `$1..$9`/`$ARGUMENTS` template, `@file` includes, embedded `` !`shell` ``,
  args-first expansion) expand and ride the `CommandResult.effect`
  `{"kind":"deliver"}` channel so both seams send them to the agent as a
  message (Claude-Code parity); plus a `@command` decorator beside
  `@workflow`/`@hook`/`@tool`, auto-registered on the plugin import sweep. Both
  plug into 2A's `source`-tagged registry, now with a full precedence rule
  (builtin > user > plugin) in `register()`. Palette (2D) color-codes the three
  sources. Boot-load at TUI `on_mount` + `serve`; no live watch. Spec:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2c.md`.
- [x] **2D — discovery UX** *(shipped)* — inline **drop-up** command palette:
  type `/` and a panel rises above the input with fuzzy-matched commands,
  subverbs, and **live argument values** (agent/session/queue/group/schedule/
  terminal/theme names). Built on one pure `complete(text, bridge)` + an
  `Arg.completer` seam (static tuple or `(bridge) -> choices`) + a `fuzzy`
  scorer; TUI (`CommandPalette` widget + `GrowingInput.key_interceptor`) and
  web (`complete` RPC + drop-up `<div>`) render the same data. Spec:
  `docs/superpowers/specs/2026-07-17-aegis-slash-commands-2d-command-palette-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-aegis-slash-commands-2d.md`.

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

### ✅ Web client S9.0–S9.2 — TUI becomes a WS client *(shipped 2026-07-16 as `aegis --remote`)*

S9.0–S9.2 shipped 2026-07-16 as `aegis --remote` (conversation loop).
`RemoteSessionManager` implements the conversation-loop `AppBridge` subset over
`WsClient`; `SSHTunnel` handles `ssh://` forwarding; `--remote` dispatches
scheme (ws/ssh) and auto-launches a local `aegis serve` for bare localhost invocations.

Deferred: **S9.3 (aux-surface RPCs)** — queue / canvas / terminal / group
dashboards raise `RemoteUnsupportedError` in remote mode; follow-up slice needed
to expose them over the WS protocol. **S10 (default flip)** — flip `--remote`
to the default and add `--classic` fallback; needs ≥1 week of daily remote use
before committing. See `know-how/remote-tui.md` for operational details and
known limitations.

- Spec: `docs/superpowers/specs/2026-07-01-aegis-tui-ws-client-design.md`
- Live smoke (loopback + zion→vps): **not yet done** — Steps 1–2 of Task 12
  require Alex's manual verification.

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

### Session history (`Ctrl+R`)

Modal listing every user-initiated agent session (open or closed, current
process or previous); reopens via jump-to-tab, `drv.resume()`, or fresh spawn
with recorded profile + cwd. Three slices: backend reads → resume path with
`session_id` latch → close marker + preview.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-session-history-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-session-history.md`

### Aegis filesystem tool surface

Six aegis-owned tools (`aegis_bash`, `aegis_read`, `aegis_write`, `aegis_edit`,
`aegis_grep`, `aegis_listdir`) routing every agent's filesystem + shell access
through the substrate. `PermissionRouter` (`allow` / `deny` / `ask`) with TUI
inline approval. Hard Claude built-in suppression via
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

### Queue v1 polish *(shipped 2026-07-13)*

Small follow-ups on top of the shipped substrate:

- **Worker tab handle suffix** *(shipped)* — in-flight worker tabs now
  show `<queue>#<task>` in muted after the slug, via
  `QueueManager.worker_label` threaded through `_refresh_tabbar` +
  `_TabCell.render_tab`. Clears on finalize/cancel.
- **`aegis_cancel(task_id)` MCP tool** *(shipped)* — `QueueManager.cancel`
  drops pending / interrupts+closes in-flight, marks `cancelled`, delivers
  one error callback so awaiting producers unblock. Idempotent.
- **`aegis_delegate` sync wrapper** *(shipped)* — `QueueManager.run`
  enqueues (callback off) + awaits a one-shot completion subscription,
  returning the worker's result directly; optional `timeout_s`.

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
sessions are shared across TUI ↔ web.

Ten slices, S1–S10, vertical, foundation-first. Earliest "usable single-tab
web client" is end of S2; full TUI feature parity is end of S6; full
architectural unification (TUI flipped to `--remote` default with `--classic`
fallback) is S10. Each slice is an honest stop point.

- Spec: `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`
- Plan: *not yet drafted — start with S1 (theme YAML + shared render refactor)*

## Ideas — things the `generate()` seam unlocks

Unspecced, roughly ordered by how distinctively aegis-shaped they are. All
ride the one-shot structured-generation seam from
`2026-07-30-aegis-session-titles-design.md` — cheap, no session, no MCP, no
tools, `text_generation:` profile. **Nothing here belongs on the hot path of
a turn**: a one-shot call is 1–3s, so every trigger below is idle-, boundary-,
or operator-driven.

### Group reducer — `synthesize` and `dissent`

`groups/reducers.py` already has `register_reducer(name, fn)` and a
`_REGISTRY` — an open extension point. Today `wait_all` gives you `concat`
(a wall of N agents' text you re-read yourself) or `majority_vote` (only
fires when answers are string-identical, i.e. never for prose).

Two reducers, one call each. `synthesize` reconciles the members into one
answer. **`dissent` is the more interesting one** — report where they
*disagreed* and why, which is exactly what a majority vote destroys and what
you can't get by skimming four replies.

Distinctive because `aegis_group_spawn_mixed(preset=…)` can convene claude +
gemini + opencode + a local lovelaice model. Omnigent and t3code have no
groups at all; nobody else can panel four vendors and reconcile them.

### DSL structured output — collect on a logged debt

`dsl/interpreter.py` does `json.loads(_extract_json(reply))` at **three**
sites (133, 165, 229), and the DSL plan shipped with *"structured output is
prompt-engineered + parsed"* written down as a known caveat. `_run_judge`
(line 214) is a `generate(YesNo, …)` call wearing a costume.

Not a feature — makes bounded `loop`/`if` control flow trustworthy rather
than hopeful. Smallest item here.

### Session recap — "where are we right now"

After a session sits idle ~5 min, generate a short *current state* recap:
what's been established, what's in flight, what's next. For coming back to
one of ten tabs without re-reading the transcript.

Hook exists: `AgentSession._arm_idle_watcher` (`core/session.py:582`,
armed at 220/538, cancelled at 225/273) already detects idle — currently
gated on `supports_idle_events`, which a recap trigger should *not* be.

Distinct from a summary: recap is a live snapshot, regenerated as state
moves; a summary is retrospective and written once.

### Session summary — the retrospective

Written at close (or on demand), appended to the log. Turns `Ctrl+R` from a
list of handles into a searchable archive. Composes with session titles —
same record, same surface, same modal.

Regeneration should truncate the transcript from the **front**, not the back
(t3code's `preserveMessageEnd`): the end is what the summary is about.

### `/btw` — a side question that doesn't steal the turn

Ask a one-off question about what's happening *while the agent keeps
working*. Takes the last few turns as context, answers via `generate()`, and
**never touches the session** — no interrupt, no inbox delivery, no turn
consumed. "Why did it pick that file?", "is this the third retry?"

The point is the non-interruption. Today the only ways to ask are `Enter`
(queues a chip, waits for the turn to end) or `Alt+Enter` (cuts the live
turn). `/btw` is the missing read-only third option. Renders in the pane as
an operator-side aside, not as a conversation turn.

Open: how many turns of context, and does the answer persist in the
transcript or vanish on scroll?

### Loop auto-evaluate — an independent judge, not the same agent

`aegis_loop_stop(from_handle, reason)` (`mcp/server.py:1248`) has the agent
reap **its own** loop, and `core/loop.py` only otherwise stops at
`max_iterations` (`exhausted`, line 35). So the entity deciding "is the goal
met?" is the entity that wants to stop working — and the failure mode is
documented in this workspace's CLAUDE.md: a 20-iteration loop stopped at
iteration 1 with the user-visible half unbuilt, because a narrow reading of
the instruction was defensible *to the agent that wrote it*.

Idea: on each would-be-idle boundary, a **separate** one-shot call gets the
loop instruction plus a digest of what's happened, and answers "goal met?".
The agent can still call `aegis_loop_stop`, but a disagreeing judge re-arms
the loop with the gap named.

The failure mode to design against is the inverse — a judge that never
lets go. Needs a cap, and the judge's verdict surfaced in the loop strip so
it's arguable rather than silent.

## Backlog

### Subscription-backed models (Antigravity / gateway) — DEFERRED INDEFINITELY

Gemini CLI subscription access died 2026-06-18; the `gemini --acp` driver is
now dead weight for subscription users. Replacement `agy` (Antigravity CLI,
v1.1.2, installed on zion) **dropped ACP** — only `--print` one-shot +
`--continue` resume, no stream-json, no per-session MCP injection. `agy models`
brokers a multi-provider pool under one Google quota (Gemini 3.x, **Claude
Opus/Sonnet 4.6 Thinking**, GPT-OSS), but the free/Pro quota is now ~20 req/day
— marginal value. Deferred indefinitely.

**Path C, zero driver code — WIRED + SMOKE-TESTED 2026-07-24.** Front a local
OpenAI-compatible gateway (OmniRoute, `localhost:20128/v1`) that harvests the
`agy` OAuth token, and point the existing **Lovelaice provider** at it via its
`base_url` + `api_key_file` fields (`config/__init__.py:67-71`,
`drivers/lovelaice.py:26-39`). Keeps full aegis-MCP injection / streaming /
idle events.

Declaring such an agent in `.aegis.yaml` was impossible until `449ebbb`:
`yaml_loader._PROVIDERS` never registered `lovelaice`, so `provider: lovelaice`
died with "unknown provider" — the provider model, driver, and driver registry
all existed, only the YAML loader's dict was missing it. That two-line fix is
the entire aegis→gateway wiring.

Proven end-to-end over OpenRouter (qwen3-32b): a lovelaice worker ran bash,
wrote files, called `aegis_list_sessions`, and delivered a message to a live
Claude peer via `aegis_handoff`. Findings:

- **`api_key_file` is mandatory even for a keyless gateway** — without it
  lovelaice's OpenAI client dies at ACP session start as an opaque
  `RequestError: Internal error`.
- **`aegis_handoff` does not validate `from_handle`** — an unregistered handle
  was accepted and routed. Possible spoofing surface; see Watching.
- qwen3-32b leaks `<tool_call>` fragments into the text channel (tools still
  execute) — the shim-parsing risk the handoff warned about is real, and
  OmniRoute's headline models are exactly that reasoning shape.

Remaining: OmniRoute itself has **zero connected providers**, so every model
returns an empty response until a provider is signed in at `localhost:20128`.
Local profile staged inert at `.aegis/agents/omni.yaml.disabled`.

Full capture, recipe, and risks:
`vault/+/agent_drafts/handoffs/handoff-2026-07-22-0830-aegis-subscription-models.md`

### Shrink the injected surface: MCP plane behind a discoverable index

The plane registers **65 tools** (`src/aegis/mcp/server.py`), and every session
also gets `PRIMING` appended to its system prompt (`server.py:370`) while
`aegis_meta` returns a `BRIEFING` well over 12k chars (`server.py:125`). A
worker that inlines all of that pays for it before doing any work.

Measured 2026-07-24: a single lovelaice turn (write a file, run bash, call two
aegis tools) cost **87.7k input tokens** — dominated by tool schemas, uncached
on that path.

**The cost is harness-dependent, which is the useful part.** Claude Code
already defers MCP tool schemas — inside aegis a Claude session sees only tool
*names* and must call `ToolSearch` to load a schema before use. lovelaice has
no such mechanism, so it inlines all 65. Same plane, very different bill. So
the fix lands in one of two places:

- **aegis-side (helps every harness):** expose a small always-on core
  (`aegis_meta` + a search/fetch pair) and serve the rest on demand — the
  deferred-tool pattern Claude Code uses. Also worth trimming `PRIMING` and
  `BRIEFING`, which are prose-heavy and re-sent per session.
- **lovelaice-side:** teach `lovelaice.mcp` lazy schema loading, so it gets
  the Claude-Code behaviour for free against any MCP server.

Prompted by Alex after the OmniRoute smoke — matters most for cheap/quota-bound
workers, where 88k/turn undercuts the whole premise of a cheap worker (the
`agy` pool is ~20 req/day). No plan yet; measure before and after.

### New `agy` driver (Path A) — noted for the future

A dedicated Antigravity driver that drives the `agy` binary directly
(`--print` + `--continue`/`--conversation`), notably unlocking **Claude
Opus/Sonnet 4.6 and Gemini via the Google subscription** without a gateway.
Downside vs Path C: no ACP ⇒ likely no aegis-MCP tools for those workers (probe
`agy plugin import claude` as a possible MCP route). Not now — noted for when
subscription access is worth a real driver-build.

### Move the Claude driver off `claude -p` — noted for the future

Today `ClaudeDriver` shells out to `claude -p --input-format stream-json
--output-format stream-json --verbose` (subprocess kept alive per session,
resume via `--resume <id>`, interrupt via a stream-json control_request —
`src/aegis/drivers/claude.py:199-232`). A future rework would drive Claude a
different way — most likely the **Claude Agent SDK** (aegis already runs
*inside* it) or an **ACP-native** path that unifies Claude with the
gemini/lovelaice drivers on one protocol (`AcpDriver`), instead of parsing the
CLI's stream-json. Motivation: robustness + protocol unification, less
dependence on the `claude -p` CLI surface. Not now — noted for the future.

## Watching

- **`aegis_handoff` accepts an unregistered `from_handle`.** During the
  2026-07-24 OmniRoute smoke a lovelaice worker passed
  `from_handle="lovelaice-probe"` — not a live session — and the message was
  accepted and routed to the target's inbox, attributed to that name. Handy
  for out-of-band probes, but it means any agent on the plane can attribute a
  message to any handle. Filing, not acting on it yet; decide whether the
  sender should be validated against the live session registry (or stamped by
  the substrate rather than passed by the caller).

- **VPS job-crawler dispatched the plan job (2026-05-20-aegis-task-queue-plan)
  but never picked up its follow-up implement job** (file existed on VPS
  with `status: armed` and `fire_at` in the past, crawler was healthy
  and firing every 60s). One-off so far; needs a closer look at the
  crawler's eligibility logic if it happens again. Filing here, not
  acting on it yet.
