# TUI performance audit — many tabs, long histories

*Status: findings complete, nothing implemented. 2026-07-29, against v0.27.0
(Python 3.13.1, Textual 8.2.6, rich 14.3.3).*

Five independent audits, one per dimension: periodic work, transcript
structures, the event-ingest hot path, tab-count fan-out, and boot/state
growth. Every number below was measured, not reasoned about; benchmark
scripts are under `.playground/perf-audit/<dimension>/` and are re-runnable.

**Measurement caveat.** The audits ran concurrently on zion (4-core i7-6820HQ)
at load 5–12, so absolute microsecond figures are roughly 2x pessimistic
against an idle machine. The *ratios* and *scaling exponents* are the result
and are hardware-independent.

## The headline

**Four of the five audits independently identified the same root cause**, by
three different methods (cProfile, isolated timing, A/B monkeypatch):

`ConversationPane.refresh_metrics()` runs after every ingested event and
locates its status bar with `self.query(StatusBar)` — Textual's *deep,
uncached, CSS-matching* walk over every descendant of the pane. A pane's
descendants are its mounted transcript blocks. So the cost of handling one
streamed token grows linearly with how long you have been in that tab, and
plateaus exactly where `N_MAX = 300` caps the window.

`_working_indicator()` has the identical bug and fires on every mounted block.
The same pattern repeats at four colder call sites.

| mounted blocks | cost per event |
|---|---|
| 0 | 3.3–3.5 ms |
| 100 | 13.9 ms |
| 300 (the cap) | 28.2–36.2 ms |
| 600 (scrolled up, see F2) | 88.9 ms |

Independent measurements of the available win: **11.8x** (ingest, 290 blocks),
**16.6x** (tabs, hidden pane), **12–30x** (timers, 1 Hz tick), **9.4x** on a
whole turn and **30x** on streaming (transcript). Textual's `query_one` *is*
cached and searches breadth-first, so it never descends into the transcript at
all: measured flat at 4–11 µs regardless of transcript length.

At a full window one streamed 4,000-char reply plus 20 tool calls costs
**~6 s of blocked event loop, per pane, additively**. That is the reported
symptom, quantitatively.

## Findings, consolidated and ranked

### 1. Uncached DOM queries on the per-event path ★★★★★

`pane.py:838` (`refresh_metrics`), `pane.py:909` (`_working_indicator`), plus
`:845`, `:851`, `:1265`, `:1532`. Called from every branch of
`_on_core_event` (`:1291,1295,1300,1306,1337`) and from `_mount_block`
(`:944`).

Fix: hold a direct reference to the `StatusBar` (composed once, never
replaced) and the `WorkingIndicator`, or fall back to `query_one_optional`,
which is cached. ~5–20 lines. Confidence very high, risk very low — all call
sites already do `.first()`, so plural semantics are unused.

Also: `_quota_tick` (`app.py:777-780`) pushes `set_quota(())` every tick even
when quota is disabled, paying a full walk to deliver a value that never
changes. One early return.

### 2. Replay renders the entire history to mount ten blocks ★★★★★

`_mount_replay` (`pane.py:773-833`) calls `render_event()` on every event in
the log to build `_history`, then mounts only `REPLAY_TAIL = 10`. For
assistant text that constructs a `rich.markdown.Markdown`, which parses
eagerly. The docstring justifies this as cheap because it avoids *widgets* —
but the cost is in the *renderables*, not the widgets.

Measured on real logs from `Workspace/.aegis/state`:

| log | size | events | blocking cost |
|---|---|---|---|
| `husky-hopper` | 24.8 MB | 17,990 | **4.35 s** (68–75% in `render_event`) |
| synthetic 5k | 4.0 MB | 5,005 | **11.5 s** (98.6% in the fold) |

`_resume_agent_tabs` (`app.py:471-497`) does this per tab, serially, on the
event loop, **including tabs mounted hidden** (`:496` sets `display = False`).
Eight tabs at p75 log size = **11.8 s of dead UI**; eight largest = 19.7 s.

Fix: `BlockRecord` carries the source events; render lazily at
materialization. `_load_older` is already the natural materialization point.
Prototyped: **30–127x** faster, 20x less retained memory. Then defer
`_mount_replay` to a pane's first `on_show` so boot is O(1) in tab count.

Risk medium — `_fold_into` builds `Group(rec.renderable, r)`, so a record must
hold a list of pending events, and `_load_older`/`_render_tool_block` index
`_history` absolutely. Deserves its own TDD cycle.

### 3. `Ctrl+R` reads and decodes every session log, on the event loop ★★★★☆

`list_history` (`history.py:135`) globs every `*.jsonl` and fully decodes each
one to extract a handle, two timestamps and a preview. `action_open_history`
(`app.py:838`) is a bare `@work`, and `textual.work` defaults to
`thread=False` — verified against the installed Textual, not assumed — so it
runs on the loop.

Against the operator's real corpus (232 logs, 615 MB): **25 s warm, 60 s
cold, whole UI frozen.**

Fix, in two steps: `@work(thread=True)` first (one line — converts a freeze
into a responsive background load), then a sidecar
`.aegis/state/history_index.json` keyed on `(size, mtime)`, re-folding only
changed logs. Prototyped: **0.3 s**, index 114 KB. A head+tail read was also
prototyped and rejected: only 4.2x and *lossy* (mismatched `last_ts` on 24 of
232 logs).

### 4. `TabBar._refresh_cells` re-renders every cell on any tab event ★★★★☆

`widgets.py:236-244` loops all cells unconditionally; each `render_tab` does a
markup parse plus `refresh(layout=True)` against `width: auto`. Fires on every
`PaneStateChanged` — twice per turn per pane.

| tabs | `_refresh_cells` | burst (2N refreshes) |
|---|---|---|
| 10 | 4.7 ms | 127 ms |
| 20 | 11.7 ms | 451 ms |
| 40 | 19.9 ms | **1363 ms** |

Fix: keep `_prev_items` and skip unchanged cells. ~6 lines, measured 300–400x
reduction. `set_palette` must clear the memo.

### 5. Eviction is gated on `_stick_to_bottom` ★★★★☆

`pane.py:954-957` nests the `N_MAX` check inside the stickiness branch, so
scrolling up during a live turn lets the mounted window grow without bound —
measured 252 → 652 blocks and still climbing. Combined with finding 1 this is
O(events²) for the scrolled-up interval.

Fix: bound the window regardless of stickiness, compensating `scroll_y` the
way `_load_older` already does (`pane.py:894-903`). Risk medium — eviction
while not pinned can jump the viewport; needs a test.

### 6. The file indexer walks the whole cwd at boot ★★★☆☆

`app.py:406` starts it on `Path.cwd()`; readiness is gated on the *complete*
walk (`file_index.py:122-123`). On the operator's Workspace: **60,025 files,
17–43 s**, and being a Python thread it taxes the event loop through the GIL —
p50 tick latency 0.35 ms → 2.20 ms (6.3x), worst case 47 ms. It overlaps
exactly with the boot replay of finding 2.

Also: the watch is unfiltered (74,134 dirs including `.git`, `.venv`), and
`_add` re-sorts the whole path list per filesystem event — **9.58 ms per
event** at 60k paths, under a lock the picker also takes.

Fix: publish incrementally instead of gating on the full walk; `bisect.insort`
instead of `append + sort`; filter the watch; consider `git ls-files` when the
cwd is a repo.

### 7. Smaller, cheap, worth doing ★★☆☆☆

- **Backtick extraction is quadratic per message.** `update_content`
  (`pane.py:317`) re-scans the whole accumulated text per streamed delta.
  Masked today by finding 1; becomes the leading term after it lands (6.5 s
  for a 205 KB message). Defer to `_flush_streaming` or make it lazy — the
  tokens are only read by `on_click` and the tooltip.
- **`session_log.append_event` does 5–6 syscalls per event** (`mkdir` + `open`
  + `write` + `close`), synchronously on the loop: 270 µs, vs 50 µs with a
  held-open fd. The durability design (per-record `os.write` on `O_APPEND`,
  fsync only on barriers) is *correct* and should stay; only the fd handling
  changes. Hoisting the redundant `mkdir` alone is one line for 45 µs/event.
- **`_record_session_closed` (`app.py:690`) fully replays a transcript** to
  evaluate two booleans — 937 ms on the largest log, on every tab close.
  `scan_log` + a type-tag check is ~4x cheaper; caching the answer is free.
- **`LOAD_BATCH = 100`** mounts 100 widgets synchronously: a **366 ms** freeze
  per scroll-up. Halve it, or slice with an await.
- **`TerminalTab` and `FileTab` never freeze their timers when hidden** — the
  0.24.0 freeze covers `ConversationPane` only. Fix TerminalTab; FileTab costs
  0.005% CPU and its fix changes behaviour (a background tab stops noticing
  external edits), so leave it.
- **`self.bell()` fires for any pane finishing** (`app.py:1159`), including
  background queue workers — a stream of BELs with many workers. Rate-limit or
  restrict to the active pane.
- **`_write_snapshot` debounce.** This was the pre-audit prime suspect and the
  measurement **falsified it**: 1–3 ms firing 2–3.5x/s, ~1% of wall time.
  Worth debouncing eventually; not a cause.

### 8. State grows without bound ★★☆☆☆

No retention, rotation, or cap anywhere in `state/`: 232 logs / 615 MB
accumulated over 68 days = **9 MB/day**. Every cost above is linear in corpus
size, so `Ctrl+R` reaches ~10 min at 10x. Given the "a transcript is the only
copy of a conversation" doctrine, prefer gzip-archiving closed logs over
deleting; `scan_log` needs a two-line gzip branch. Separately, consider
capping `ToolResult.text` at persist time — one 20.2 MB log holds only 2,025
records.

## Verified already optimal — do not touch

- **The stream parser and driver pump** (~32 µs/event, 0.2–0.6% of a core).
  The 16 MiB `_STREAM_LIMIT` is a correct choice.
- **Observer fan-out** — 2–3 callbacks per event, none proportional to history
  or tab count. The *absence* of an app-level per-event hook is why the
  many-tabs symptom comes from per-pane work, not fan-out.
- **The 0.24.0 background-timer freeze** — verified with 20 tabs all mid-turn:
  timers fired 10/s total, i.e. exactly one pane's worth. Zero tab-count
  scaling. Idle background tabs are genuinely cheap.
- **Markdown-parse-once (0.24.0)** — verified: `Markdown.__init__` parses into
  `self.parsed`, `__rich_console__` never re-parses, `update_content` does not
  reparse, scrolling does not reparse.
- **Renderable sharing** — `_history` and the widget hold the *same* object
  everywhere. No duplication.
- **`scan_log`'s streaming read** — 3.4 MB peak to sweep 615 MB, and the
  damage-tolerance path costs nothing on healthy logs.
- **`QueueDigest`** (push-based), **`QuotaService`** (60 s, off-thread,
  backoff handled), **`sample_system`** (549 µs at 1 Hz), **transcript
  windowing design**, **`_transcript()` and `scroll_end()`** (free at any
  window size).

## Recommended order

1. **Cache the widget lookups** (finding 1). ~20 lines, 9–30x on the hottest
   path, near-zero risk. Do it first and re-measure everything else, since it
   changes the baseline for every other finding.
2. **Memoize the tab-cell render** (finding 4). ~6 lines, removes the
   0.45–1.4 s burst freezes.
3. **`@work(thread=True)` on history** (finding 3, step one). One line, turns
   a 25–60 s freeze into a background load.
4. **Lazy `BlockRecord` renderables + defer replay to `on_show`**
   (finding 2). The big one: resume and boot. Own TDD cycle.
5. **Bound the window when scrolled up** (finding 5), **halve `LOAD_BATCH`**,
   **defer backtick extraction** (finding 7).
6. **History sidecar index** (finding 3, step two), **file-indexer**
   (finding 6), **retention** (finding 8).

Findings 1 and 2 together address most of the reported sluggishness.

## A note for whoever implements this

`AGENTS.md` now states that a red suite run is a regression, not noise. Guard
these fixes with tests that assert *structure* rather than wall-clock — e.g.
that `refresh_metrics` cost is flat in `len(pane._mounted_blocks)`, or that it
calls `query_one` rather than `query`. A timing assertion would flake on a
loaded box, as this audit's own discarded wall-clock numbers demonstrate.
