# aegis bench: release-over-release performance benchmark

*Status: implemented 2026-09-14. Plan:
`docs/superpowers/plans/2026-09-13-aegis-bench.md`. The implementation
notes at the end record where the build departed from this text.*

## Why

Two perf audits (`2026-07-29-tui-performance-audit.md`, three rounds) fixed
real costs, and both were measured with headless `App.run_test`
benchmarks. Headless Textual returns early from `App._display`
(`textual/app.py:3833`), so it never measures content height, never renders
strips and never encodes ANSI. Round 3 shelved the Line API transcript
rewrite on a 6.3 ms frame figure that is a lower bound for exactly that
reason. Round 2 timed `pilot.pause()`, which costs O(mounted widgets),
and its milliseconds were 10x too large.

We have no number that describes what the operator sees, and no way to
tell whether a release made rendering, CPU or memory better or worse.
`aegis bench` is that number, runnable with every release.

## What it measures

A benchmark run drives a real `aegis serve` and a real `aegis` client in a
pty, the same process topology an operator uses. Nothing runs headless.

| Group | Metrics |
|---|---|
| Rendering | frame tick time, split into layout, compositor and display; frames per second; bytes per frame; layout passes per second; `get_content_height` and `render_lines` call counts |
| Latency | marker-to-bytes (a token leaving the fake agent until its bytes reach the terminal); keystroke echo; resize to first complete frame; queueing delay at each daemon hop |
| Event loop | lag p50, p99 and max; stalls per minute over 16, 50 and 100 ms; GC pause total and max |
| CPU | daemon and client CPU seconds per wall second, separately |
| Memory | daemon RSS and USS, peak and at the end; RSS growth per 1,000 events |
| Startup | cold daemon boot to socket ready; attach to first complete frame |
| Host | CPU busy share in the window, from `/proc/stat`; load average per core. Both are neutral context, and a run over 50% busy is flagged |

The terminal emulator's own drawing is excluded. It is the same cost for
every TUI, and a pty cannot observe it.

## Architecture

Everything lives in `src/aegis/bench/`, so `aegis bench` works from an
installed package. Production code gets no benchmark hook.

### The world

Each scenario repeat builds a throwaway world under `/tmp/aegis-bench-*`:

- a generated `.aegis.yaml` with `bench` agent profiles (claude and
  lovelaice drivers);
- `AEGIS_DAEMON_DIR` pointing inside the world, `AEGIS_IDLE_TIMEOUT=0`;
- a `bin/` directory first on `PATH` holding the fake `claude` and the fake
  `lovelaice-acp`.

The world is outside `/home/apiad/Workspace` because aegis config lookup
walks up to the closest `.aegis.yaml`. The bench starts the daemon itself,
kills it by PID, and never reads or writes the operator's daemon registry.

### Units

| Unit | Purpose | Depends on |
|---|---|---|
| `world.py` | Build the temp root, config, `bin/` shims, env; start the daemon through the launcher; wait for the socket; tear down by PID | `launcher.py` |
| `launcher.py` | Start `aegis serve` with `probe.py` installed first, for the current install or a `--target` (a version via `uvx`, or a path to another interpreter) | `probe.py` as a file |
| `probe.py` | Standalone, no aegis imports. Wraps Textual methods with timing spans, runs the loop-lag sampler, registers `gc.callbacks`, samples psutil each second, optionally wraps the streaming paint with a sabotage sleep. Writes JSONL. Fails loudly if a required hook is missing | Textual, psutil |
| `rig.py` | The terminal stand-in. Owns a pty, runs the client, stamps every chunk with `time.monotonic_ns()`, answers `\e[?2026$p`, splits frames at `\e[?2026l`, detects markers in ANSI-stripped frames, injects keys, resizes with `TIOCSWINSZ` | ptyprocess or `pty` |
| `fake_claude.py` | Installed as `claude`. Speaks `claude -p` stream-json on stdin/stdout, replays a fixture turn by turn at recorded timing (scaled by a speed factor), embeds markers, logs each marker's emit time | fixtures |
| `fake_acp.py` | Installed as `lovelaice-acp`. An ACP v1 agent over the `agent-client-protocol` SDK that streams `AgentMessageChunk` updates with markers | `agent-client-protocol` |
| `fixtures/` | Recorded stream-json sessions with per-line relative timestamps. One recorded without and one with `--include-partial-messages` | none |
| `scenarios.py` | Declarative scenario timelines: world setup, actions (type, key, resize, attach a second client, wait for marker), measurement window | `rig`, `world` |
| `metrics.py` | Raw JSONL to a summary: per-metric p50/p95/p99/max per repeat, median across repeats, plus the environment fingerprint | none |
| `compare.py` | Two summaries to per-metric verdicts | `metrics` |
| `report.py` | Rich tables for run, compare and history; `report.md` | `metrics`, `compare` |
| `record.py` | Run the real `claude` on a fixed prompt and store a fixture | real `claude` |
| `cli_bench.py` | The typer subapp, registered in `cli.py` | all |

### Data flow for one repeat

1. `world.py` builds the root and starts the daemon via `launcher.py`, with
   `AEGIS_BENCH_PROBE=<run>/probe.jsonl`.
2. `rig.py` starts `aegis` in the pty at the scenario's size, with the
   world as cwd. It answers the DEC 2026 query and waits for the first
   complete frame.
3. The scenario types `/spawn bench <prompt>` and Enter, like a user.
4. The daemon spawns the `claude` shim. The shim replays its fixture and
   appends `{marker, t_emit_ns}` to `<run>/emit.jsonl`.
5. The rig records `{t_ns, frame_bytes, markers_seen}` per frame to
   `<run>/frames.jsonl`.
6. After the scenario's end condition, the rig detaches the client and
   `world.py` kills the daemon. `metrics.py` joins emit, frames and probe
   records by marker and by monotonic time.

`time.monotonic_ns()` uses `CLOCK_MONOTONIC`, which every process on the
host shares, so emit and frame timestamps are directly comparable.

### Markers

A marker is `«bNNNN»`, short enough never to wrap. The fake agent inserts
one every N characters of text. Marker-to-bytes latency is the first frame
whose stripped text contains the marker, minus its emit time.

A marker can go undrawn for a real reason: text that scrolls past between
two repaints, or a reply rendered in one piece when the turn ends. The
first real `acp-stream` run showed the second: ACP sessions queue every
chunk until the prompt returns, so 55 of 100 markers were never on screen.
The undrawn share is reported as `latency.markers_undrawn_pct`. Only a
stream in which no marker is ever drawn fails the run, because that says
the rig cannot see markers at all.

## Scenarios

| Name | Default set | What happens |
|---|---|---|
| `startup` | yes | cold daemon boot to socket; attach to first frame; detach and re-attach to a warm daemon |
| `idle` | yes | three tabs with finished turns, nothing happening, 20 s |
| `claude-blocks` | yes | the no-partial fixture: whole text blocks and tool calls, real timing |
| `block-stream` | yes | 120 short synthetic blocks at 50 ms; the recorded session yields only two to four markers, too few for percentiles |
| `claude-stream` | yes | the partial fixture. The shim emits `stream_event` lines only when its argv has `--include-partial-messages`; today aegis does not pass it, so the scenario reports `skipped` with that reason |
| `acp-stream` | yes | the lovelaice driver against `fake_acp`, chunks at 50 per second |
| `deep-stream` | yes | a fast first turn mounts about 300 blocks, then a measured streaming turn |
| `resize` | yes | at about 300 blocks: five resizes and five sidebar toggles |
| `typing` | yes | 200 characters at 20 per second into the input while `acp-stream` runs |
| `many-tabs` | yes | one visible tab and six background tabs streaming |
| `two-clients` | yes | a second client attaches mid-stream; both are measured |
| `soak` | no | ten minutes of 4x replay; memory growth per 1,000 events |

`--quick` runs `startup`, `block-stream`, `acp-stream` and `resize` once.

## CLI

```
aegis bench run [--scenario S ...] [--quick] [--repeat N] [--size COLSxROWS]
                [--target VERSION|PYTHON] [--speed X] [--profile]
                [--sabotage MS] [--save] [--out DIR]
aegis bench list
aegis bench compare A B            # run ids, paths, or --baseline latest-release
aegis bench history [--metric M]
aegis bench record [--partial] [--prompt TEXT]
aegis bench selftest
```

Defaults: `--repeat 3`, `--size 120x40`, `--speed 1.0`.

`--profile` runs the daemon under `py-spy record` (via `uvx py-spy` when
not installed) and writes a speedscope file into the run directory.

`--sabotage MS` makes the probe add a sleep of MS milliseconds to every
streaming paint. It exists so `selftest` can prove the rig sees a
regression.

## Storage

- A run writes `~/.aegis/bench/runs/<run-id>/`: raw `frames.jsonl`,
  `emit.jsonl`, `probe.jsonl` per repeat, plus `summary.json` and
  `report.md`.
- `--save` copies `summary.json` to `bench/history/<host>/<version>-<sha>.json`
  in the aegis repo (resolved from the running package's source tree; the
  flag errors when aegis is not an editable checkout).
- Every summary carries a fingerprint: host, CPU model, core count, CPU
  governor, terminal size, Python, Textual and Rich versions, aegis
  version, git SHA, `aegis.__file__` as seen by the daemon.

## Comparing

`compare` refuses to compare summaries from different hosts or terminal
sizes unless `--force` is given.

Per metric, with each run holding one value per repeat:

- **timing and resource metrics**: `regressed` or `improved` when the
  median moved by more than 10% *and* by more than the metric's absolute
  floor (for example 0.5 ms for frame time, 2 MB for RSS) *and* the two
  runs' repeat ranges do not overlap. Otherwise `noise`.
- **call counts**: compared exactly; any change is reported as `changed`
  with the ratio.

`history` prints one row per saved release for a chosen metric set, in
version order.

## Validity gates

A repeat fails, and the run exits non-zero, when any of these is false:

1. The rig saw `\e[?2026h` in the output, and the probe reports
   `app._sync_available` true.
2. The probe reports `app.is_headless` false.
3. Every required probe hook installed.
4. When markers were emitted, at least one was drawn (`markers_seen`).
5. The daemon's `aegis.__file__` matches the target the launcher was asked
   for.

`aegis bench selftest` runs a synthetic block stream plain and with
`--sabotage 40`. It passes only when marker-to-bytes p50 rises by at least
30 ms and the probe's sabotaged span p50 rises by at least 36 ms. There is
no upper bound: a sleep on the event loop also delays whatever queues
behind it, so the latency rise can legitimately exceed the sleep.

## Targets

`--target` runs the benchmark against another aegis build. The rig,
fixtures and fake agents come from the running bench; only the aegis
processes come from the target. A version string runs
`uvx --from aegis-harness==VERSION python`, and a path runs that
interpreter. Aegis-internal probe hooks are optional per target; Textual
hooks stay required.

A target runs in one of two topologies, chosen by introspecting it:

- **daemon**: the target's `aegis.cli` has an `attach` command. The bench
  starts `aegis serve` with the probe installed and attaches `aegis attach`
  in the pty. `aegis attach` first shipped after v0.37.0 (commit
  `fb5189c`, 2026-09-12).
- **in-process**: no `attach` command. The pty runs the target's `aegis`
  directly, and the probe is installed in that process, which holds the
  brain and the only view. Daemon hops, `two-clients` and warm re-attach
  do not apply and report `skipped`.

The topology is part of the fingerprint, so a comparison between the two
shows what the daemon path costs.

## Testing

Unit tests under `tests/bench/`, all hermetic:

- frame splitting on `\e[?2026l` across chunk boundaries;
- marker detection through ANSI sequences and split writes;
- the fake claude's stream-json parsed by `aegis.events.parse` into the
  expected event types;
- percentile, median-of-repeats and verdict logic in `compare.py`;
- probe hook installation fails loudly against a class missing a method.

One slower integration test, marked so the default suite skips it, runs
`claude-blocks` with `--repeat 1 --speed 8` end to end.

Timings are never asserted in tests. CI runner timing is noise.

## Release procedure

`know-how/releasing.md` gains a step before tagging: run
`aegis bench run --save` on zion, then commit the history file with the
release.

## Out of scope

- An HTML trend report. `history` prints a table.
- Measuring other TUIs. The rig could time Claude Code's frames, but no
  marker emit times exist for it.
- Changing aegis rendering. The benchmark measures; fixes are separate
  work judged by its numbers.

## Implementation notes (2026-09-14)

The build followed this design, with these departures, each forced by a
measurement.

- **Typing into a cold view.** A cold attach leaves focus on the tab bar,
  so the rig clicks the input before typing. It locates text from the
  cursor moves in the raw frames.
- **Tabs.** Ctrl+T in a daemon view opens the tab in the background.
  Scenarios move to it with Ctrl+Right, confirm the switch on a screen
  reconstructed from every frame (`ScreenGrid`), and time it as
  `tabs.switch_ms`. A prompt that lands in an existing tab fails the run,
  because a failed switch otherwise passed silently, twice.
- **`block-stream`.** The recorded `claude-blocks` session holds a single
  4,882-character text block, one marker per repeat, so 120 synthetic
  blocks carry the latency percentiles.
- **Host state.** `host.cpu_busy_pct` and `host.load_per_core.max` are
  recorded per window and the fingerprint samples the busy share before a
  run. The warning uses the busy share: at load 7.2 on 8 cores a window was
  17% busy with quiet-host latency.
- **Fixtures** keep only assistant, user and stream_event lines; a recorded
  `rate_limit_event` had reached aegis as an unknown event.
- **Teardown** sends SIGINT before SIGTERM, because py-spy writes its
  profile only on a graceful stop, and polls the daemon so an unreaped
  zombie does not hold every wait to its timeout. `profile_written` gates
  `--profile`.
- **Failures leave evidence.** A failed repeat writes
  `screen-<client>.txt` beside its traceback.

The first runs also measured aegis itself. None of this is fixed by the
bench:

- a cold attach leaves the input unfocused (`_mount_brain_pane` focuses
  only with `foreground=True`, and the observer worker never passes it);
- Ctrl+T in a daemon view opens the new tab in the background, the same
  cause;
- ACP replies render only when the turn ends: `AgentSession._run_turn`
  awaits `send`, which awaits `conn.prompt` for the whole turn
  (`acp-stream`: no paint for 10 s, 55 of 100 markers never drawn);
- a second attached view stops drawing a live stream about 0.4 s after
  attaching (`two-clients`: 92.8% of its markers undrawn);
- `import aegis.cli` takes 2.1 s, most of a warm re-attach;
- a 4.9 KB Markdown block cost a 212 ms frame tick, and GC pauses reached
  138 ms under `many-tabs`.
