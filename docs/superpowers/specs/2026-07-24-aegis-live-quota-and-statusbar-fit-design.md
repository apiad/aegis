# Live Claude quota in the status bar (and a bar that fits)

- **Status:** design
- **Date:** 2026-07-24
- **Target version:** 0.22.0
- **Author:** Alex + Claude

## Problem

Aegis shows what a session *cost* but never what it has *spent* against the
subscription. `Metrics` reports tokens, dollars and tool counts per session;
`/usage` aggregates `.aegis/state/sessions/*.jsonl` into retrospective cost
analytics. Neither answers the question that actually changes what you do
next: **how much of the 5-hour window is left?**

Anthropic exposes this. `GET https://api.anthropic.com/api/oauth/usage`, with
the OAuth token from `~/.claude/.credentials.json` and the
`anthropic-beta: oauth-2025-04-20` header, returns live utilization for the
session and weekly windows plus their reset times. It is undocumented and
subject to change, which shapes the error handling below but not the design.

Adding a segment to a bar that already overflows would make things worse, so
this spec carries a second, coupled change: the status bar learns to fit
itself to the terminal.

## Part 1 — The quota segment

### Data source

New module `src/aegis/usage/quota.py`, beside `aggregate.py` / `cost.py` /
`env.py` / `render.py`.

Aegis is a public PyPI package and cannot depend on `bin/claude-usage` from
Alex's Workspace, so the fetch is reimplemented in-repo — roughly fifty lines
against `urllib`, adding no dependency. `CLAUDE_CREDS` remains honoured as a
path override, matching the existing tool.

Three units:

```python
def read_token(path: Path) -> str | None
```
Pure file read; returns `None` for a missing file, unparseable JSON, or an
absent `claudeAiOauth.accessToken`. Never raises.

```python
def fetch_quota(token: str, *, timeout: float = 10.0) -> QuotaSnapshot
```
The HTTPS call. Raises `QuotaError(kind=...)` where `kind` is `unauthorized`
(401/403) or `unreachable` (anything else — timeout, DNS, 5xx, malformed
payload).

```python
@dataclass(frozen=True)
class QuotaSnapshot:
    windows: tuple[QuotaWindow, ...]   # every entry in limits[]
    fetched_at: float                  # monotonic
```

`QuotaWindow` carries `kind` (`session`, `weekly_all`, …), `percent`,
`severity`, `resets_at`, `is_active`.

The snapshot is built from the response's **`limits[]` array**, not from the
`five_hour` / `seven_day` headline objects. Two reasons: `limits[]` is the
complete set (the live response also carries `weekly_opus` and others that go
null on some plans), and each entry carries the API's own `severity` field. So
"when does this turn amber" is Anthropic's judgment rather than a threshold
invented here. If `severity` is missing, fall back to a local rule —
`normal` < 80% ≤ `warning` < 95% ≤ `critical`.

The live payload is null-heavy: `seven_day_opus`, `extra_usage.monthly_limit`
and a dozen codename fields all return `null` on the current plan. Parsing
must treat every field as optional and drop entries it cannot read rather than
failing the whole snapshot.

### The poller

`QuotaService`, same module, shaped after `ReminderService` and `LoopService`:
one asyncio task on a 60-second cycle holding a cache.

```python
class QuotaService:
    def start(self) -> None
    async def stop(self) -> None
    async def refresh(self, *, force: bool = False) -> None
    def current(self) -> QuotaState
```

`current()` is a synchronous cache read, so the 1-second `AegisApp._tick`
never touches the network. `refresh()` respects a 60-second floor unless
forced, which makes it safe to call from a turn-end observer without a burst
of short turns hammering an undocumented endpoint.

`QuotaState` is a small union:

- `Fresh(snapshot)` — fetched within the cycle
- `Stale(snapshot, age_s)` — last good value, current fetch failing
- `Failed(kind)` — no good value to show; `kind` in
  `no_credentials` / `unauthorized` / `unreachable`

A snapshot degrades `Fresh → Stale` on the first failure and
`Stale → Failed(unreachable)` after 5 minutes of continuous failure. A
success resets to `Fresh` from any state.

### Visibility

The service polls, and the segment renders, only when **at least one open
`ConversationPane` has `_agent.harness == "claude-code"`**.

`Agent.harness` is assigned from `provider.name` in
`config/__init__.py:103`, and `ClaudeCode.name` is the literal
`"claude-code"`, so this is a real field rather than string-sniffing a model
name.

Scoping is global rather than per-tab on purpose. The quota is an account
property, and the case where it matters most is a background Claude worker
eating the window while you sit in a terminal or file tab — precisely what a
per-tab rule would hide. It also avoids a segment that flickers in and out as
you tab around, which reads as a glitch rather than information.

The corollary matters for cost: a session with no Claude agent anywhere makes
**zero network calls**. The service is not started until the predicate first
goes true.

### Rendering

Calm:

```
⧗ 5h 64% · wk 7%
```

Once a window reaches `warning`, that window — and only that window — grows a
reset countdown:

```
⧗ 5h 87% ⟶2h14m · wk 12%
```

"When does it reset" is a question you only ask when the number is high, so it
costs no width until it earns it. Severity drives colour via the existing
`AegisColors` roles (`themes/__init__.py:13`): `warning` renders in
`colors.working`, `critical` in `colors.error`, everything dimmed uses
`colors.muted` — the same three roles `sysmeter.format_system` already uses.

Failure renders a dimmed message rather than disappearing, so a broken
integration is distinguishable from a quiet one:

```
⧗ quota — no credentials
⧗ quota — auth expired
⧗ quota — unreachable
```

A `Stale` state keeps the last good numbers, dimmed, with their age:

```
⧗ 5h 64% · wk 7% (2m old)
```

A single failed poll therefore shows a slightly aged number, not an alarm;
only sustained failure escalates to `unreachable`.

### Turn-end refresh

`AgentSession.add_state_observer` (`core/session.py:147`) already feeds
`ConversationPane._on_core_state` (`pane.py:533`). A Claude session
transitioning out of the working state calls `QuotaService.refresh()`,
floor-guarded. That is the moment the number actually moves and the moment you
are most likely to look at it.

### The command

`quota` joins `_VIEWS` in `src/aegis/commands/builtins/usage.py`, giving
`/usage quota`. It prints every window in `limits[]` — not just the two
headline ones — with percent, severity, absolute reset timestamp and
countdown, plus the age of the reading. It forces a refresh, because an
explicit ask should never return a cached value.

This reaches the web client for free: `CommandResult` already crosses that
seam, so `/usage quota` works in the browser on day one even though the
status-bar segment is TUI-only.

## Part 2 — A status bar that fits

### The problem, measured

A realistic bar today, before adding anything:

| segment | rendered | cols |
|---|---|---|
| identity | `aegis 0.21.0  claude-opus-4-8  high` | 35 |
| state | `working` | 7 |
| metrics | `~↑73.9K (80% cached) ↓12.4K (80% think) · ⚡ 42 tok/s · ctx 88.2K (44%) · $3.42 · ⚒ 27 (1 err) · 1m12s / 43m10s` | 110 |
| loop | `⟳ loop 3/20` | 11 |
| sysmeter | `CPU 23% · RAM 38% · DSK 71%` | 27 |

With four-space separators that is ≈226 columns. It already overflows a
120-column terminal, and `StatusBar._refresh` (`widgets.py:284`) concatenates
unconditionally, so the overflow is silent — Textual clips it and you lose
whatever sits on the right.

### The fit engine

New module `src/aegis/tui/fit.py`, deliberately free of any Textual import so
it can be tested as a pure function.

```python
@dataclass(frozen=True)
class Segment:
    key: str
    tiers: tuple[str, ...]   # widest first; last is the narrowest form
    priority: int            # higher survives longer

def plain_width(text: str) -> int
def fit(segments: Sequence[Segment], width: int, sep: str = "    ") -> str
```

`plain_width` strips Rich markup before measuring — the regex currently inline
in `StatusBar.render_plain` (`widgets.py:279-282`) moves here and both callers
share it.

`fit` starts with every segment at tier 0 and, while the joined width exceeds
the budget, repeatedly takes the **lowest-priority segment that can still
degrade** and advances it one tier; when a segment is at its narrowest it is
dropped entirely; and it never drops the highest-priority segment, truncating
as an absolute last resort. `width <= 0` means "unmeasured" and renders
everything at tier 0 — which is what happens before mount, and keeps every
existing `render_plain` test passing unchanged.

### Priorities

Ranked by *does this change, and does it demand action* — not by how
interesting it is:

| priority | segment | rationale |
|---|---|---|
| 70 | connection banner | only exists when something is broken |
| 60 | state | 7 columns, the most-glanced thing on the bar |
| 50 | loop | only exists when armed; when armed it is driving the session |
| 40 | quota | changes, and past 80% it changes what you do next |
| 30 | metrics | degrades internally in stages (below) |
| 20 | identity | static — you know what you launched |
| 10 | sysmeter | ambient, and a real terminal exists if you need it |

The two static segments sit at the bottom deliberately: on a narrow terminal
you should lose the things that never change and keep the things that do.

### Tiers per segment

**identity** — `aegis 0.21.0  claude-opus-4-8  high` → `opus-4.8 high` →
`opus-4.8` → dropped. A `short_model(name)` helper strips the `claude-`
prefix and rewrites a trailing `-4-8` as `4.8`.

**metrics** — `Metrics` grows `render_tiers(now) -> tuple[str, ...]`, with
`render()` kept as `render_tiers()[0]` so nothing else changes:

- T0 — today's full string
- T1 — drop the tool segment and tok/s
- T2 — also drop the cached and think shares, and `ctx` loses its absolute
  figure (`ctx 88.2K (44%)` → `ctx 44%`; the percentage is what you act on)
- T3 — `↑73.9K ↓12.4K · $3.42`, the irreducible core

**quota** — `⧗ 5h 64% · wk 7%` → `⧗ 64/7%` → dropped.

**sysmeter** — `CPU 23% · RAM 38% · DSK 71%` → `23·38·71%` → dropped.

**state**, **loop**, **connection banner** — single tier each; already terse.

Full bar at T0 for every segment is ≈150 columns after the shrink, down from
≈226, so a 120-column terminal loses only sysmeter and part of metrics rather
than silently clipping the right-hand third.

### Wiring

`StatusBar` keeps its `set_*` setters and its per-segment fields, but
`_refresh` builds a `Segment` list and calls `fit(segments, self.size.width)`.
An `on_resize` handler re-runs `_refresh`, so splitting the terminal re-fits.
`set_quota(state)` joins `set_system` / `set_loop` (`widgets.py:250-260`),
with a `ConversationPane.set_quota` passthrough mirroring `pane.py:695`.

`AegisApp` owns the `QuotaService` lifecycle and pushes into the active pane
from `_tick` (`app.py:649`), exactly where `sysmeter` is sampled today.

## Testing

- `fetch_quota` against a stubbed opener replaying **recorded real payloads**,
  including the null-heavy live response, a 401, and a truncated body.
- `QuotaService` cadence, the 60-second floor, and every state transition,
  driven by an injected clock. No network in the suite.
- Rendering is pure — state in, string out — with one test per branch
  including all three failure kinds, the stale-with-age form, and the
  countdown appearing only at `warning`.
- `fit()` as a table test: a segment list plus a width, asserting the exact
  output at 226, 150, 120, 80 and 40 columns, and that `state` survives at 20.
- The visibility predicate over a synthetic pane list, no running app.

## Out of scope

- **Web status-bar parity.** The segment is TUI chrome; `/usage quota` already
  gives the web client the data. A follow-on slice.
- **Threshold alerts into the conversation** — a notice when a window crosses
  80% during a long autonomous run. Real design questions attached (where it
  lands, how often, whether it interrupts); its own spec.
- **Non-Claude quota.** No other harness exposes an equivalent endpoint.
- **Configurable thresholds.** The API's `severity` is the source of truth.

## Open question

For a user running Claude Code on an API key rather than a subscription,
`read_token` returns `None` forever and the bar carries a permanent
`⧗ quota — no credentials`. That is the intended behaviour here — a dimmed
message beats silence when something is misconfigured — but if it proves to be
noise rather than signal, the escape hatch is a `quota: false` config key.
Not built in v1.

## Risk

The endpoint is undocumented. If Anthropic changes or removes it, the failure
path already designed here is exactly what fires: the segment degrades to
`⧗ quota — unreachable` and nothing else in aegis is affected. No parsing
failure can propagate into the render loop — `_tick` reads a cache, never the
network.
