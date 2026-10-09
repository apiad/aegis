# aegis: agents see aegis running

**Status: part 1 implemented, 2026-10-09** (issue #234), following
`docs/superpowers/plans/2026-10-09-agents-see-sessions.md`. Part 2 (issue
#235) is designed and has no plan yet. Designed with Alex in a brainstorm,
text only.

## What this delivers

An agent inside aegis can answer questions about the server it runs on and
advise on them: how the quota is going and who spent it, what RAM and CPU did
over the last hour or the last week, which session holds the memory, and what
every other session is doing right now. It can also wait for a set of other
sessions to finish and then do the work it was asked to do afterwards.

Two requests drove the design:

- "How is my quota going? What RAM have we had in the last hour?" asked in any
  tab, answered from data aegis kept, not from a guess.
- "When everything working on une-tools finishes, cut a release." The agent
  picks the sessions, waits without polling, and is woken when they are done or
  when one of them is blocked on Alex.

**Read and advise, Alex acts.** No tool added here changes another session:
nothing pauses, closes, throttles or kills. The agent reports and recommends;
work it was asked to do (tagging, `gh release`) is its ordinary work, not
control over aegis. This keeps out the question of which session may stop
another.

**Each server answers for itself.** A linked server shares only handles and
states of its sessions on purpose (`links.py`); none of the new data crosses a
link.

## What exists today

Measured from a live session on 2026-10-09.

- `quota_read` returns the last reading per provider and window, with severity,
  projection and reset time. No history.
- `HostSampler` (`host.py`) reads CPU, RAM and disk every 5 s, only while a
  browser subscribes to the `host` channel, and keeps the last value. No MCP
  tool reads it.
- `session_list` returns handle, title, state, cwd, worker and `you`. The
  session holds much more: `cost_usd`, `context_tokens`, model and
  `last_activity` in `Session.meta()`, and the attention card from
  `attention.card()` with `attention`, `attention_line`, `waiting_on`, the plan,
  `plan_now`, `plan_did`, `plan_done`, `plan_total` and `plan_clock`.
- A monitor's conditions are bash (`MonitorStart.done`), and bash cannot ask
  aegis anything: the CLI has `serve`, `doctor` and `init` only.
- Per-session process resources are not measured at all. `Session.pid` gives
  the root of each session's process tree.
- State is JSON and JSONL under `<state>/`; aegis has no database and this
  design adds none.

## Part 1: other sessions' state, and waiting on them

### `session_list` carries the card

Each entry for a session on this server adds:

| field | from |
|---|---|
| `model` | `model_id`, else the spec's model |
| `cost_usd`, `context_tokens`, `context_window` | `Session.meta()` |
| `last_activity` | `Session.meta()` |
| `attention`, `attention_line`, `waiting_on` | `attention.card()` |
| `plan`, `plan_now`, `plan_did`, `plan_done`, `plan_total`, `plan_clock` | `attention.card()` |

`state` stays as it is. Entries for linked servers stay cut to handle and
state. `replies`, `mark` and `blink` are left out: they describe what the
person's tab shows, not what the session is doing.

`attention_line` and the plan are text another agent wrote. On one server that
is already the case for `peer_read`; the rule that keeps far text out
(`far_sessions`) is unchanged.

### A monitor that waits on sessions

A new tool, `monitor_sessions(description, sessions, interval_s=10,
timeout_s=4h)`, arms a monitor that watches a list of handles instead of
running bash. It is a tool of its own rather than a `sessions` field on
`monitor_start` because `monitor_start` requires `progress` on purpose (#165),
and a schema where `progress` is required for one mode and meaningless for the
other is one a model reads wrong. The monitor it makes is an ordinary monitor:
`monitor_list` shows it and `monitor_cancel` stops it. The timeout defaults to
four hours because other sessions' work runs longer than a build.

Each session is kept by log id, so a watched session renamed after arming is
still followed, and listing one handle twice watches it once. A session cannot
wait for itself: its own wait would keep it `waiting` forever.

On each check, every listed session is classified from its attention card:

| class | when |
|---|---|
| finished | attention is `done` or `review`, or the session is closed |
| blocked | attention is `needs_you` or `error` |
| running | attention is `working` or `waiting` |

- All finished: the monitor ends `ok`, like a bash monitor whose `done`
  passed, naming each session and how it ended.
- Any blocked: the monitor ends with the new outcome `blocked`, naming the
  session and its `attention_line`, so the agent can tell Alex "une-base-manda
  is asking you something; the release waits". Waiting through a question would
  leave the release stalled until the timeout with nobody told.
- A handle that does not name an open session at arm time is rejected. A
  session closed later counts as finished.
- Progress is finished over listed, and the card shows "1 of 2".

The list is fixed when the monitor is armed. The agent picks the handles from
`session_list` (titles, plans); aegis cannot tell which sessions belong to a
repo, since they share a cwd. A session opened after arming is not included.

The monitor reuses everything monitors already have: the card, the wake through
the owner's inbox, the timeout, persistence in `monitors.json` and re-arming at
boot. The watching session is `waiting` on it, like any other monitor.

## Part 2: telemetry

### The recorder

A new module, `telemetry.py`, started by `app.py` next to `HostSampler` and the
quota service.

**Sampling.** Every 5 s, whether or not a browser is watching. `HostSampler`
keeps publishing to the `host` channel only while subscribed; the recorder reads
the same functions (`cpu_times`, `memory`, `disk`) itself. Each sample reads:

- host CPU, RAM, swap and the disk that holds aegis;
- for each open session with a process, the tree under `Session.pid`, found
  through the PPID field of `/proc/*/stat`, summing RSS and CPU over it. This
  counts the harness, its shells, dev servers and anything else it started;
- each session's `cost_usd` (plus `recap_cost_usd`) and `context_tokens`.

**Minute rows.** When a minute closes, one row per source:

- `host`: CPU, RAM and swap as average and maximum; disk as its last value.
- `session`, one per open session: RSS and CPU as average and maximum, cost and
  context tokens at the end of the minute, handle, log id and harness. A
  session with no process gets RSS 0 rather than a missing row, so a gap in a
  series always means the recorder was not running.

**Rows written as they happen.**

- `quota`: one row per new reading from a provider (a changed `read_at`), with
  every window's percent, projection and reset.
- `event`: session opened or closed; turn started or ended, with its duration
  and final attention; monitor ended, with its outcome; queue task started or
  ended.

**Files.** One JSONL per day, `<state>/telemetry/YYYY-MM-DD.jsonl`, one row per
line: `{"t": <epoch>, "k": "host"|"session"|"quota"|"event", ...}`. At boot and
when the day changes, files older than 7 days are deleted. Seven days because
the weekly quota window is seven days: with less, an agent cannot explain a
weekly projection. Estimated size is 1 to 2 MB per day with ten sessions open;
the plan measures it.

**Without /proc** (macOS) no host or process rows are written; quota, spend and
events still are, and the tools say "no host meters on this server".

### `telemetry_summary`

`telemetry_summary(window="1h")`, or `since` and `until` in epoch seconds for a
past slice. `window` ranges from `15m` to `7d`. One compact answer, about 40
lines:

- host: CPU, RAM and swap now, with min, average and maximum over the window
  and when the maximum happened; disk now;
- quota: per provider and window, the percent at the start and end of the
  window, the rise, the projection and the reset;
- the five sessions with the most RAM (maximum RSS) and the five with the most
  spend, each with its share of aegis's spend on its provider;
- up to 20 events, those nearest the RAM and CPU maxima first;
- notes: skipped bad lines, gaps where nothing was recorded, no host meters.

Sessions closed during the window appear by handle, so "which session ate the
RAM yesterday" works after it is gone.

**Attributing spend.** A session's share is its rise in cost (own turns plus
recaps) over the window, divided by the rise of all sessions on the same
provider: Claude Code sessions count toward Claude, OpenCode sessions toward
OpenCode Go. This is a share of aegis's spend, not of the quota: the same
Claude account is spent by VPS jobs and terminals outside aegis, and nothing
here can see them. The output says so in a note. No conversion from dollars to
quota points is estimated.

aegis returns facts. The tool does not say "close this session"; the agent
advises from the facts plus the severity and projection the quota already
computes.

### `telemetry_series`

`telemetry_series(metric, since, until=now, session=None)` returns
`[[t, avg, max], ...]`.

Metrics: `host.cpu`, `host.ram`, `host.swap`, `host.disk`; `session.rss`,
`session.cpu`, `session.cost`, `session.context` (these need `session`, a
handle); `quota.<provider>.<window>`.

The step is chosen to keep about 120 points (1 minute over two hours, 15
minutes over a day). Over a step, `avg` is averaged and `max` keeps the
maximum, so a 20-second spike survives any step.

## Errors

The recorder never stops `aegis serve`.

- A bad JSONL line (a write cut by a shutdown) is skipped when read and counted
  in the summary's notes.
- A failed write (disk full) logs one line and keeps sampling into memory; the
  summary reports the gap.
- A process that exits between two reads of `/proc` is skipped for that sample.
- An unknown metric, handle or a window out of range is an `OpError` naming
  what is valid.

## Testing

The tests run the real app, as `AGENTS.md` asks: `aegis serve` with the fake
claude calling the real `/mcp`.

- **Parsers**: the process tree and the minute aggregation, from fixed `/proc`
  text, the way `host.py` is tested. The clock and the interval are injected, so
  a test minute lasts 0.2 s.
- **Tools**: two fake sessions spending known amounts; the test session calls
  `telemetry_summary` and `telemetry_series` and checks the shares and maxima.
  Expected values are computed from the module, not repeated as literals.
- **Session monitor**: one of two sessions finishing leaves the monitor
  running; the second ends it `done`; a session turning `needs_you` ends it
  `blocked`. A browser test checks the card reads "1 of 2".
- **Break it on purpose**: divide by the total across providers and confirm the
  attribution test fails.
- **`make test-live`**: a real Claude Code session asked "what RAM have we had
  in the last hour?" answers through `telemetry_summary`.
- **`make bench`**: the cost of sampling every 5 s with ten sessions open; the
  table goes in the PR body.

Done means what `AGENTS.md` says, ending with Alex running the branch through
`aegis-dev`. For part 1 the real test is his request: "when the une-tools
sessions finish, cut a release".

## Left out

- Control tools: pause, close, throttle or kill another session.
- Docker containers and network meters.
- Telemetry from linked servers.
- A browser view of the history; the Fleet band keeps showing the current
  meters only.
- A CLI for reading telemetry from bash.
