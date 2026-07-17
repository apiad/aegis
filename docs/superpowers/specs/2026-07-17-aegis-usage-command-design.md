# `aegis usage` — session usage & cost analytics

- **Status:** design
- **Date:** 2026-07-17
- **Target version:** 0.19.0 (v1)
- **Author:** Alex + Claude

## Problem

Aegis persists a rich per-tab event stream to `.aegis/state/sessions/<handle>.jsonl`
(via `aegis.state.session_log`), but nothing surfaces it as aggregate
insight. There is no way to answer "what have my sessions cost", "where do
my tokens go", "which tools drive spend", or "how is my usage trending".
The data to answer all of these already exists on disk — it just has no
reader.

This spec designs a read-only `aegis usage` command (v1 CLI, v3 TUI modal)
that aggregates the session logs into a usage-and-cost dashboard plus a set
of deeper analytical cuts.

## Key data-model findings (validated against real logs)

A playground pass over 188 real session logs (2026-05-21 → 2026-07-17,
4,462 `Result` turns) established the ground truth this design rests on:

1. **Cost is already recorded, authoritatively.** Each `Result` event
   carries `cost_usd` — claude-code's own `total_cost_usd`. It is
   **cumulative per claude-code run, with resets**: within one session a
   resume starts a fresh cumulative counter, so a session's log can contain
   several monotonic cost segments. Correct per-session cost is therefore
   the **sum of each monotonic segment's final value**, *not* the sum of
   all `cost_usd` values (that over-counts ~19×) nor the last value (that
   drops pre-resume segments). Coverage: 98% of turns (`cost_usd` absent on
   17 old sessions / 27 turns).

2. **Model is already recorded.** `SystemInit.model` gives the real model
   per session (e.g. `claude-opus-4-7`, `claude-opus-4-8`). The window
   spans two Opus versions plus one stray `OpenCode` session. 17 old
   sessions predate the field.

3. **`Result.usage` is per-turn** (`TokenUsage`: `input`, `output`,
   `cache_creation`, `cache_read`). `cache_read` is a per-turn re-read of
   the growing cached context and dominates raw token volume (≈76% of a
   token-priced cost estimate). It is a genuine billed cost but represents
   context *replay*, not new generation — see the two-layer cost model
   below.

4. **Tools, latency, errors are all present** — `ToolUse.name`,
   `Result.duration_ms`, `Result.ttft_ms`, `Result.is_error`.

The authoritative cost total ($10,951) and a token×price-table estimate
($9,933) agree to ~9%, which cross-validates both the segment-aware
`cost_usd` handling and the price-table path used for the analytical split.

## The two-layer cost model

`cost_usd` is a single opaque number: it tells you *what you paid* but not
*why*. The token decomposition tells you *why* but only *approximates* the
bill. The command shows both, clearly labelled:

- **Billed cost (headline)** — segment-aware sum of `Result.cost_usd`.
  Authoritative; blends models correctly; this is what you actually paid.
- **Analytical split (explanatory)** — from `Result.usage` tokens priced
  against `aegis.models.get_prices(provider, model)`:
  - *generation* = `input`·in + `cache_creation`·cache_write + `output`·out
  - *replay* = `cache_read`·cache_hit

  Reported as "of your billed cost, ~X% is context replay (cache reads),
  ~Y% is generation." This is the "smart cache-read handling" — replay is
  never hidden, but it is separated from real generation so long sessions
  don't read as expensive work.

Where `cost_usd` is missing (old sessions), the billed figure falls back to
the token×price estimate, flagged `~est` in the output.

## Architecture

Read-only. No new persisted state. Three units:

### 1. `aegis.usage.aggregate` (new module) — the engine

Pure function over the on-disk logs; no I/O concerns beyond reading.

- **Input:** the state dir (resolved via `find_project_root()` →
  `<root>/.aegis/state`), optional filters (`since`, `handle`).
- **Reads:** each `sessions/*.jsonl` via the existing decode path
  (`aegis.state.event_codec.decode_event`; may reuse
  `session_log.replay_events` or a lighter streaming reader to avoid
  building full event lists for 442 MB — see Performance).
- **Produces:** a `UsageReport` dataclass:
  - `sessions: list[SessionUsage]` where `SessionUsage` carries `handle`,
    `model`, `provider`, `turns`, `tools: Counter`, `tokens: TokenUsage`
    aggregate, `billed_usd` (segment-aware), `gen_usd`, `replay_usd`,
    `est: bool` (fell back to token estimate), `duration_ms`, `errors`,
    `ttft_ms` list, `first_ts`, `last_ts`.
  - Roll-ups: totals, per-model breakdown, per-day / per-dow / per-hour
    buckets, tool→cost correlation, cost distribution percentiles.
- **Cost helpers:**
  - `segment_cost(cost_usd_sequence) -> Decimal` — the segment-aware sum.
  - `token_cost(usage, prices) -> (gen, replay)` — the analytical split.
- **Model→price resolution:** `SystemInit.model` → normalize to a registry
  key (strip date suffixes, map `claude-opus-4-8` → the entry via its
  `aliases`) → `get_prices(provider, model)`. On unknown model, skip the
  analytical split for that session (billed cost still works) and note it.

### 2. `aegis.cli_usage` (new module) — the CLI surface

Mirrors `cli_models.py` / `cli_budget.py`. A typer subapp registered in
`cli.py` with `app.add_typer(_usage_app, name="usage")` alongside the
existing `models` / `budget` / `schedule` subcommands.

- **Bare `aegis usage`** → the dashboard (one screen):
  headline billed cost + generation/replay split; running averages
  (per session, per turn, per day); per-model breakdown; tool histogram
  (top ~12); top-5 sessions by billed cost; rolling-7-day sparkline;
  error rate; window + session count.
- **Flags for deeper cuts:**
  - `--by month|dow|hour` — temporal bar charts (turns + billed cost).
  - `--sessions [N]` — cost distribution (p50/p90/p99/max + histogram) and
    top-N session table.
  - `--tools` — tool→cost correlation (avg turn cost when a tool is
    present, vs baseline; Pearson r of tools-per-turn vs cost).
  - `--since <YYYY-MM-DD>` — window start.
  - `--session <handle>` — single-session detail.
  - `--model <key>` — filter to one model.
  - `--json` *(v2)* — machine-readable dump for Telegram/job consumers.

Rendering reuses `aegis.tui.metrics._fmt_cost`, `_fmt_tokens`, `_fmt_time`
for consistency with the live TUI footer. ASCII bar/sparkline helpers live
in `cli_usage` (small, presentational).

### 3. TUI `/usage` modal *(v3)*

A `ModalScreen` mirroring the existing `QueueDashboard`
(`aegis.tui.dashboard.QueueDashboard`, pushed via `self.app.push_screen`).
Renders the same dashboard roll-ups from the same `aggregate` engine, so
CLI and TUI never diverge. Slash-command wiring follows however
`/`-commands currently reach `_apply_command_effect`
(`aegis.tui.pane`) — exact hook located at plan time.

## Timezone

Temporal bucketing (day / day-of-week / hour) converts each event's
`aegis_ts` (UTC) to the **system local timezone** via
`datetime.astimezone()` with no argument — no hardcoded zone, so the report
reflects wherever it runs. An optional `--tz <IANA name>` overrides, for
analyzing one host's logs from another zone.

## Performance

442 MB across ~190 files, scanned in <2 s in the playground with naive
`json.loads` per line. v1 may reuse `replay_events`, but that decodes full
`Event` objects and builds per-session lists. If startup latency matters,
the engine streams lines and pulls only the needed fields
(`t`, `usage`, `cost_usd`, `model`, `name`, `duration_ms`, `is_error`,
`aegis_ts`) without full decode. Decision deferred to the plan; both are
fast enough. No caching — recompute on each invocation (data is local, cost
is a couple seconds).

## Error handling & edge cases

- **Corrupt / partial lines** — skip silently (a session log may end
  mid-write); never abort the whole report for one bad line.
- **Missing `cost_usd`** (17 old sessions) — fall back to token×price
  estimate, flag `~est` per session and in totals.
- **Missing `SystemInit.model`** (17 old sessions) — fall back to
  `.aegis.yaml` `default_agent`'s model for the analytical split; label
  the model column `?→<assumed>`.
- **Multi-model sessions** — keyed per session by `SystemInit.model`; a
  session that resumed under a different model is rare but attributed to
  its init model (documented limitation, not worth per-turn model tracking
  in v1).
- **Non-claude drivers** (the lone `OpenCode` session) — no `cost_usd` /
  registry entry; shown with billed `—` and excluded from cost roll-ups,
  counted in turn/tool stats.
- **Empty / shell sessions** (0 turns, 0 tools) — filtered out.
- **No state dir / no sessions** — friendly "no session logs found"
  message, exit 0.

## Testing

- **Unit — cost engine:** `segment_cost` on synthetic sequences —
  monotonic, single reset, multiple resets, empty, single value. This is
  the correctness-critical function; the 19× over-count bug it prevents is
  the whole reason it exists.
- **Unit — token split:** `token_cost` against known usage + a fixed price
  stub; assert generation/replay partition.
- **Unit — aggregation:** a small fixture dir of hand-written `.jsonl`
  sessions (2–3 sessions, mixed models, one with a resume/reset, one
  missing `cost_usd`, one empty) → assert `UsageReport` totals, per-model
  breakdown, `~est` flagging, filtered empties.
- **Unit — temporal bucketing:** fixed `aegis_ts` values + forced `--tz`
  → assert day/dow/hour placement.
- **Smoke — CLI:** run `aegis usage` and each flag against the fixture dir;
  assert exit 0 and key figures appear. No golden-string assertions on
  ASCII charts (brittle); assert numbers and section headers.
- Follows the aegis test convention (`uv run pytest`); no network, no real
  session data in tests.

## Phasing

- **v1 (0.19.0)** — `aggregate` engine + `aegis usage` CLI with the
  dashboard and all deeper-cut flags. Authoritative billed cost
  (segment-aware) + generation/replay split + per-model breakdown +
  temporal / tool / distribution cuts. All from data that already exists.
- **v2** — robustness & interop: `--json` output; tighten the missing-data
  fallbacks; optional per-turn model tracking if multi-model sessions prove
  common.
- **v3** — TUI `/usage` command → `ModalScreen` dashboard reusing the v1
  engine.

## Non-goals (YAGNI)

- No cross-workspace / cross-host aggregation (state is per-project, local).
- No persistence, caching, or incremental index — recompute each run.
- No historical price reconstruction — use the current registry; `cost_usd`
  is already the historical truth for the billed headline.
- No budgeting / alerting (that is `aegis budget`'s domain).
- No export formats beyond `--json` (v2), added only if a consumer needs it.

## Real symbols this design touches

- `aegis.state.session_log` — `session_log_path`, `replay_events`,
  `EventReplay`; logs at `<root>/.aegis/state/sessions/<handle>.jsonl`.
- `aegis.state.event_codec.decode_event`.
- `aegis.events` — `Result` (`duration_ms`, `is_error`, `input_tokens`,
  `output_tokens`, `usage`, `cost_usd`, `ttft_ms`, `num_turns`,
  `stop_reason`, `model_usage`), `SystemInit` (`session_id`, `model`),
  `ToolUse` (`name`, `summary`, `usage`), `AssistantText`/`Thinking`,
  `TokenUsage` (`input`, `output`, `cache_creation`, `cache_read`).
- `aegis.models` — `get_prices(provider, model)` → `ProviderPrices`
  (`input`, `output`, `cache_hit`, `cache_write`; `Decimal`).
- `aegis.config.find_project_root` (`src/aegis/config/__init__.py:134`).
- `aegis.cli` — typer `app`, `app.add_typer(..., name=...)` pattern
  (existing: `models`, `budget`, `schedule`, `config`, `plugin`,
  `workflow`).
- `aegis.cli_models` / `aegis.cli_budget` — subapp precedents to mirror
  (`cli_budget._load_jsonl` reads state jsonl in a command already).
- `aegis.tui.metrics` — `_fmt_cost`, `_fmt_tokens`, `_fmt_time`.
- `aegis.tui.dashboard.QueueDashboard` — `ModalScreen` precedent for v3.
