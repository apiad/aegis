# Live Terminals

A **terminal** is a real shared PTY that Alex and any agent can spawn,
run commands on, send raw keystrokes to, read history from, and
subscribe to. When a command finishes, every subscriber except the
writer wakes up with an inbox message carrying the cmd, exit code, and
output tail — same `✉` channel queues, handoffs, and canvas writes
already use.

Aegis has six coordination primitives now:

| Primitive | Verb | Wake trigger |
|---|---|---|
| Queue | "do this, tell me when done" | Worker completes |
| Inbox / handoff | "wake — message for you" | Sender posts |
| Canvas | "wake — shared state changed" | Subscriber writes |
| Workflow | "run this orchestration, callback when done" | Workflow returns |
| **Terminal** | "wake — command finished here" | Command exits |
| Groups | "fan this out to a committee, gather replies" | Members reply |

## The model

- A terminal wraps a real PTY-backed shell (bash or zsh). The
  underlying process keeps state — `cd`, exported vars, sourced
  configs, virtualenvs — between commands.
- Command boundaries are detected via [OSC 133 shell integration
  escape sequences](https://gitlab.freedesktop.org/Per_Bothner/specifications/blob/master/proposals/semantic-prompts.md):
  the spawned shell prints invisible markers before/after each
  command and on each prompt. Aegis parses those bytes deterministically
  to know when a command started, ended, and what its exit code was.
- Every command is recorded to an append-only JSONL ledger. Reading
  past commands is fast and survives restarts.
- Subscribers wake on command-finish. The writer's own commands don't
  echo back into their own inbox.

## MCP tools

| Tool | Args | Returns |
|---|---|---|
| `aegis_term_spawn` | `name`, `shell` (optional), `cwd` (optional), `from_handle` | `{name, pid, shell, cwd, started_at, ...}` |
| `aegis_term_list` | — | list of terminal metadata |
| `aegis_term_run` | `name`, `cmd`, `from_handle`, `timeout` (optional) | `CommandRecord` (see below) |
| `aegis_term_keys` | `name`, `keys`, `from_handle` | `{ok}` — raw bytes to PTY |
| `aegis_term_read` | `name`, `last_n` or `since_seq` | list of `CommandRecord` |
| `aegis_term_subscribe` | `name`, `from_handle` | `{ok, subscribers}` |
| `aegis_term_unsubscribe` | `name`, `from_handle` | `{ok}` |
| `aegis_term_close` | `name`, `from_handle`, `purge` (optional) | `{ok}` |

A `CommandRecord` is:

```json
{
  "seq": 4,
  "cmd": "pytest -q",
  "writer": "agent:alice",
  "started_at": "2026-05-22T14:03:21Z",
  "finished_at": "2026-05-22T14:03:25Z",
  "duration_s": 4.2,
  "exit": 0,
  "stdout": "...",
  "stderr": "",
  "killed_by_restart": false,
  "timed_out": false
}
```

`from_handle` is the calling agent's aegis handle (read from the
system prompt). It's used as the **writer** on the record and to
suppress the writer's own inbox echo.

## Notifications

When agent **alice** runs `pytest -q` in a terminal that **bob** is
subscribed to, bob's inbox receives:

```
✉ from term:build · 2026-05-22T14:03:25Z
  $ pytest -q  · run by agent:alice
  exit 0 · 4.20s
  ──
  ......                                                       [100%]
  6 passed in 4.18s
```

The body shows the tail of stdout (last ~8 lines) and, when present, a
short stderr block. The full output is in the ledger via
`aegis_term_read`.

If aegis is restarted, the ledger persists but live PTYs and
subscribers don't — `aegis --resume` re-spawns saved terminals as
fresh shells over their existing ledger, and any commands that were
in flight are marked `killed_by_restart: true`.

## Worked example

```python
# PM spawns a build terminal and asks a build agent to run the suite.
aegis_term_spawn(name="build", from_handle="pm")
aegis_term_subscribe(name="build", from_handle="pm")
aegis_handoff(target_handle="builder",
              context="run pytest in terminal 'build'",
              from_handle="pm")

# builder (woken by handoff)
rec = aegis_term_run(name="build", cmd="pytest -q",
                     from_handle="builder")
# rec.exit, rec.stdout, rec.duration_s are immediately available.

# PM wakes with:
> from term:build · 2026-05-22T14:03:25Z
  $ pytest -q  · run by agent:builder
  exit 0 · 4.20s
  ──
  6 passed in 4.18s
```

For raw-key interaction (e.g. interrupting a long-running process):

```python
aegis_term_run(name="build", cmd="sleep 30", from_handle="builder",
               timeout=2.0)
# Returns with timed_out=True. Send Ctrl-C through the raw channel:
aegis_term_keys(name="build", keys="\x03", from_handle="builder")
```

## TUI surface

`Ctrl+E` opens a name prompt and creates a `term:<name>` tab. Each
command renders as a block (header / output / footer chip). The input
bar has two modes:

- **run** (default): Enter submits the line as a command.
- **raw** (`Ctrl+K` toggles): every keystroke is sent verbatim to the
  PTY — useful for `vim`, `htop`, interrupt sequences, etc.

Past commands are visible above a `── live ──` separator and stay
clickable for copy-to-clipboard.

## State on disk

```
.aegis/state/terminals/<name>/
  meta.json              # {name, shell, cwd, started_at, version}
  ledger.jsonl           # one append per finalized command
  raw.log                # raw PTY bytes (debugging / replay)
  init.sh                # bash rcfile injecting OSC 133 (bash only)
  .zdotdir/.zshrc        # zsh rcfile injecting OSC 133 (zsh only)
```

Live terminals are listed in `workspace.json` so `aegis --resume`
restores them. `--clean` skips that section. The state dir is
gitignored by aegis defaults.

## Limitations (v1)

- **bash + zsh only.** Other shells start without OSC 133 integration;
  `aegis_term_run` still works for one-shot commands but command
  boundaries are best-effort.
- **No live output streaming over MCP.** `aegis_term_run` returns on
  command completion (or timeout). For incremental output, agents can
  poll with `aegis_term_read since_seq=N` — full streaming is on the
  follow-up list.
- **No subscription persistence across restarts.** Re-subscribe on
  each session.
- **No multi-terminal session groups.** Each terminal is independent
  — no broadcast-run-here API yet.

## Full spec

See `docs/superpowers/specs/2026-05-21-live-terminals-design.md`.
