# Benchmarking the TUI (`aegis bench`)

*When to reach for it: measuring rendering, latency, CPU or memory;
comparing a change or a release against the last one; or before claiming
anything got faster or slower.*

`aegis bench` drives a real `aegis serve` and a real client in a pty and
reports what reaches the terminal. The design is in
`docs/superpowers/specs/2026-09-13-aegis-bench-design.md`.

## Running it

```
aegis bench run --quick                  # startup, block-stream, acp-stream, resize; 1 repeat
aegis bench run                          # the default set, 3 repeats
aegis bench run -s block-stream -s resize --repeat 5
aegis bench list                         # scenarios and recent runs
aegis bench compare RUN_A RUN_B          # exit 1 when a metric regressed
aegis bench compare RUN --baseline latest-release
aegis bench history -s block-stream      # saved summaries on this host
aegis bench selftest                     # prove the rig sees a 40 ms regression
```

Runs land in `~/.aegis/bench/runs/<run-id>/`: one directory per scenario
and repeat, with `summary.json` and `report.md` at the top.
`AEGIS_BENCH_HOME` moves that root, which keeps throwaway runs out of the
list.

Each repeat builds a world under `/tmp/aegis-bench-*` with its own daemon,
a fake `claude` and a fake `lovelaice-acp` on `PATH`, and
`AEGIS_DAEMON_DIR` inside the world. It never touches the daemon you are
using. `--keep` leaves the world behind for inspection.

## Reading a summary

Every number comes from the scenario's measurement window, so boot and
history preload are excluded. Medians are across repeats, and the
`repeats` column shows the spread.

- `latency.marker_ms.*` is the time from a token leaving the fake agent
  until its bytes reach the pty. It stops at the terminal's input; the
  emulator's own drawing is the same for every TUI and is not measured.
- `latency.markers_undrawn_pct` is the share of markers never drawn. That
  is data, not a broken run: a reply rendered in one piece when its turn
  ends shows only its tail. The run fails only when no marker is drawn.
- `render.tick_ms.*` and its layout, compose and display split come from
  the in-process probe on Textual's frame tick.
- `loop.lag_ms.*` and `loop.stalls_*` measure how late a 5 ms sleep on the
  daemon's event loop wakes.
- `host.cpu_busy_pct` is how busy the whole machine was in the window.

A run exits 1 when a gate fails: the rig saw no DEC 2026 synchronized
output, the probe reported sync off or headless, no marker was drawn,
keystrokes did not echo, or `--profile` wrote no profile. A failed repeat
leaves `error.txt` and `screen-<client>.txt`, the reconstructed screen at
the moment of failure. Read the screen before guessing.

## Comparing

`compare` calls a metric `regressed` or `improved` only when the change
exceeds 10% of the baseline, exceeds the metric's absolute floor, and the
two runs' repeat ranges do not overlap. With one repeat a side the last
test rejects nothing, and `compare` says so. Use three repeats or more for
anything you will act on.

Frames per second, ticks per second and host load have no better
direction, so they report `changed`.

## The host has to be quiet

Timings do not compare across a busy machine. `run` samples the CPU busy
share for a second before starting and warns above 50%; `compare` notes a
run whose window was over 50% busy.

Look at the busy share, not the load average. On zion, a run at load 7.2
on 8 cores had a 17% busy window and quiet-host latency, while two runs at
load 10 measured `block-stream` p50 at 28 ms and 141 ms. Other sessions
running test suites or measurement jobs are the usual cause, and
`ps -eo pcpu,args --sort=-pcpu | head` shows them.

## Other builds

`--target 0.37.0` measures a release from PyPI through `uvx`, and
`--target /path/to/python` measures another install. A build whose CLI has
`attach` runs as daemon plus client. Anything older runs the in-process
TUI, and `two-clients` reports `skipped`. The fingerprint records the
topology, so comparing the two shows what the daemon path costs.

## Profiling

`--profile` runs the daemon, or the in-process client, under
`py-spy record` and writes `profile.speedscope.json` beside the repeat.
Open it at https://www.speedscope.app. Profiling roughly doubles marker
latency, so never compare a profiled run with an unprofiled one.

py-spy writes its file only on a graceful stop, which is why teardown
sends SIGINT before SIGTERM. When the `profile_written` gate fails, read
py-spy's own lines at the top of `serve.log`.

## Fixtures

`claude-blocks` and `claude-stream` replay sessions recorded from the real
`claude`, stored in `src/aegis/bench/fixtures/`. `aegis bench record` and
`aegis bench record --partial` re-record them, which costs a few cents.
The recorder keeps only assistant, user and stream_event lines, scrubs the
scratch and home directories, and refuses to write a fixture when the
session ended with an error. Before committing a fixture, check that
`grep -c /home/ src/aegis/bench/fixtures/*.jsonl` prints 0 for each file.

## Saving history for a release

`--save` copies the summary into the checkout the running aegis was
imported from, under a `bench/history/<host>/` directory. A dev build is
named `<version>-<sha>.json`, with `-dirty` when `src/` has uncommitted
changes, and a `--target X.Y.Z` run is named `X.Y.Z.json`. The release
step is in `know-how/releasing.md`.

## Traps

- Run from a clean tree. Other sessions edit the shared checkout, and a
  half-applied edit anywhere in `src/aegis` breaks every run with an
  import error. A `git worktree` at your commit with its own `uv sync`
  isolates the bench from that.
- The bench types the way a user does, and works around four aegis
  behaviours it found on 2026-09-13, none fixed yet:
  - a cold attach leaves focus on the tab bar, because
    `_mount_brain_pane` focuses the input only when `foreground=True`, so
    the rig clicks the input before typing;
  - Ctrl+T in a daemon view opens the new tab in the background, so the
    rig moves to it with Ctrl+Right and confirms the switch on screen;
  - ACP sessions render a reply only when the turn ends
    (`AgentSession._run_turn` awaits `send`, which awaits `conn.prompt`),
    so `acp-stream` shows about half its markers undrawn;
  - a second attached view stops drawing a live stream shortly after it
    attaches, visible as `latency.markers_undrawn_b_pct` in `two-clients`.

  When one of these is fixed, its number moves. That is what the bench is
  for.
- `claude-stream` reports `skipped` until aegis passes
  `--include-partial-messages` to `claude`. The fake decides from the argv
  it was started with, not from reading aegis's source.
- `render.height_calls` and `render.render_lines_calls` count the base
  `Widget` methods only; subclasses that override them are not counted.
- Never time `pilot.pause()` or run Textual headless for performance.
  Headless skips content measurement, strip rendering and ANSI encoding.
