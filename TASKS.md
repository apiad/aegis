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

### Plugin substrate v1 *(slice 1 of 5 shipped)*

Hooks + tools + plugin install/update/uninstall + registry resolver + canonical
`skill-system` plugin. Slice 1 (hooks substrate — `@hook` decorator, composer,
runner, `pre_turn` / `post_turn` / `session_end` wired into `AgentSession`)
already landed (commits `8a9b206` → `75076f5`). Slice 2 (tools), Slice 3 (plugin
lifecycle + lockfile), Slice 4 (`gh:` registry resolver), Slice 5 (skill-system
canonical plugin) still owed — no `src/aegis/tools/`, `src/aegis/plugins/`, or
top-level `plugins/` on disk yet.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-plugin-substrate-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-plugin-substrate-v1.md`

### Driver visibility parity — slice 1

Make every tool call legible across drivers: semantic kind icon, path hint,
structured input retained, success/failure styling. Two ride-along bug fixes
(failed gemini events as red `└ error`, gemini turn metrics > 0). No new event
types in this slice — chunk aggregation, plan blocks, diff rendering each get
their own slice.

- Spec: `docs/superpowers/specs/2026-05-28-aegis-driver-visibility-parity-design.md`
- Plan: `docs/superpowers/plans/2026-05-28-aegis-driver-visibility-slice1.md` *(status: draft)*

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
