# Live Terminals — design spec

**Date:** 2026-05-21
**Status:** Draft, approved
**Owner:** Alex + Claude

## Summary

A fifth coordination primitive in aegis alongside inbox, queue, canvas, and
workflow. A **terminal** is a real, live PTY process owned by aegis. Both
Alex and any agent can spawn it, run commands on it, send raw keystrokes,
read its history, and subscribe to its command-finish events. Terminals
render as a new tab type in the TUI. Subscriber wakes flow through the same
`✉` inbox channel the other four primitives already use.

## Motivation

Agents already have a bash tool inside their own harnesses, but each
agent's bash is private — output is invisible to peers, state is not
shared, and there's no event surface to coordinate around. The existing
four primitives let agents pass messages (inbox), delegate work (queue),
co-edit text (canvas), and run deterministic flows (workflow) — but none
let them collaborate around a *running* shell.

Live terminals make execution itself a shared, observable surface:

- Agent A runs `pytest`; agent B is woken with the result without B
  having dispatched the command.
- Alex types in the shared `build` terminal; agents see the same screen
  and can `aegis_term_run` follow-ups.
- A `tdd-cycle` workflow can target a long-lived terminal so test runs
  accumulate in one ledger that Alex can also see.

## Non-goals (v1)

- **vt100 emulation / curses apps.** No `vim`, `less`, `htop`. Output
  renders as a stripped command-session log.
- **Daemon detach (tmux-style persistence).** TUI death kills PTYs.
  State layout is forward-compatible with a future daemon but the daemon
  is out of scope.
- **REPLs as collaboration surface.** You can `aegis_term_keys` into a
  `python` or `psql` REPL but command-finish detection won't work (no
  shell prompt OSC markers). REPL collaboration is a future primitive on
  top of terminals.
- **Fish shell support.** Bash + zsh only. Fish can be added later by
  authoring an OSC 133 init snippet for it.
- **Cross-host terminals.** Local PTYs only. SSH is the user's
  responsibility (run `ssh user@host` as a command inside a local term).
- **Resize negotiation.** Terminals spawn at fixed `cols=80 rows=24`.
  TUI resize doesn't propagate to the PTY in v1.
- **Permission / ACL.** Any agent can do anything to any terminal. No
  per-agent sandboxing — terminals run with aegis's own process
  privileges, same as the host shell.

## Design

### Concept and terminology

- A **terminal** is a named, single live PTY plus its on-disk ledger and
  raw log. Names are user-supplied (`build`, `tests`, `db-shell`) and
  unique within an aegis session. Terminals are purpose-named, unlike
  agents which receive generated handles.
- A **subscriber** is an agent (or the human, via the TUI) that has
  registered to receive an inbox wake on every command-finish event from
  a given terminal.
- A **command record** is one entry in the terminal's `ledger.jsonl`,
  bounded by OSC 133 prompt markers, containing `{seq, cmd, exit,
  started_at, finished_at, stdout, stderr, writer, duration_s,
  killed_by_restart}`.
- The **writer** field attributes a command to the entity that issued
  it: `agent:<handle>` for an MCP-dispatched `aegis_term_run`, or
  `human` for a command Alex typed into the TUI tab.

### Why OSC 133

The biggest design decision in this spec is using **OSC 133 shell
integration** to detect command boundaries and exit codes, rather than
regex-matching PS1 or wrapping every command in marker injection.

OSC 133 is the de facto standard used by iTerm2, WezTerm, Kitty, and
Ghostty for shell integration. It defines four escape sequences the
shell emits at known points in its lifecycle:

| Sequence | When | Meaning |
|---|---|---|
| `\e]133;A\a` | Just before PS1 prints | Prompt about to start |
| `\e]133;B\a` | After Enter, before command runs | User input ended |
| `\e]133;C\a` | (deprecated; not used) | Output starting |
| `\e]133;D;<exit>\a` | After command exits | Command finished, exit code N |

These are produced by tiny bashrc/zshrc snippets aegis writes at PTY
spawn. They reach the PTY master verbatim; aegis's output parser strips
them from rendered output and uses them as deterministic event markers.

Compared to alternatives:

- **PS1 regex matching** is fragile — breaks on multi-line prompts,
  custom prompts (starship, p10k), nested shells, and any time the
  prompt happens to appear in output.
- **Marker injection** (`cmd; echo END_$?`) is reliable but only works
  for commands aegis itself issues — it can't observe Alex's typed
  commands without re-wrapping his keystrokes, which is a brittle layer.

OSC 133 covers both cases identically: every prompt cycle emits markers
regardless of who typed the command.

**Fallback.** If shell integration cannot be installed (unknown shell,
user-disabled, exotic environment), `aegis_term_run` switches to marker
injection: it appends `; printf '\e]133;D;%d\a' $?` to the command
before sending. In fallback mode, subscriptions still work for
MCP-driven commands but Alex's typed commands won't emit `D` markers.

### MCP surface

Eight new MCP tools, all registered behind the same `AppBridge` seam the
other primitives use. All take an optional `from_handle` (the calling
agent's aegis handle, read from its injected system prompt).

| Tool | Args | Returns | Notes |
|---|---|---|---|
| `aegis_term_spawn` | `name: str`, `shell?: str`, `cwd?: str`, `env?: dict`, `from_handle?: str` | `{name, pid, shell, cwd, started_at}` | Errors if `name` already exists. `shell` defaults to `$SHELL` or `/bin/bash`. `cwd` defaults to aegis's launch directory. |
| `aegis_term_list` | — | `[{name, pid, shell, cwd, started_at, last_cmd_at, last_exit}, …]` | One entry per live terminal. |
| `aegis_term_run` | `name: str`, `cmd: str`, `timeout?: float`, `from_handle?: str` | `{cmd, exit, stdout, stderr, duration_s, seq, writer}` | **Blocks** until the OSC 133 `D` marker (or `timeout` elapses; on timeout returns `{exit: null, duration_s, ..., timed_out: true}` without killing the running process). Holds a per-terminal lock for the duration. |
| `aegis_term_keys` | `name: str`, `keys: str` (UTF-8 bytes), `from_handle?: str` | `{ok: true}` | Fire-and-forget. Bypasses the lock. Used for raw input — answering `[y/N]`, sending `\x03` (Ctrl-C), driving REPLs. |
| `aegis_term_read` | `name: str`, `last_n?: int = 5`, `since_seq?: int`, `from_handle?: str` | `[<command_record>, …]` | Reads from `ledger.jsonl`. `since_seq` overrides `last_n` when set. |
| `aegis_term_subscribe` | `name: str`, `from_handle: str` | `{ok: true, subscribers: [<handle>, …]}` | Idempotent. |
| `aegis_term_unsubscribe` | `name: str`, `from_handle: str` | `{ok: true}` | Idempotent. |
| `aegis_term_close` | `name: str`, `from_handle?: str` | `{ok: true}` | Sends SIGTERM to the shell, then SIGKILL after 2s. Cleans up state directory iff `purge: true` is passed (default keeps ledger). |

The `BRIEFING` block injected into every spawned agent gets a new
"TERMINALS" section documenting these tools, with the same `from_handle`
hygiene rule canvas and queue already document.

### TUI surface

A terminal is a new tab type, peer to agent tabs.

- **Spawn.** `Ctrl+T` opens the existing tab-chooser overlay, which
  gains a "Terminal…" entry alongside the agent-profile list. Selecting
  it prompts for `name` (with auto-suggest from recent names) and
  optional `cwd`.
- **Tab title.** `term:<name>` with the same state-dot system agents
  use (`●` = idle, `⠹` = command running, `*` = sticky background-
  finished bell after a command exits in a backgrounded terminal tab).
- **Body — command-session-log view.** Each command renders as a block:
  - Header line: `$ <cmd>  · <writer>  · <started_at>`
  - Body: streamed stdout/stderr, escape codes stripped (ANSI color
    *kept* and rendered via Textual's RichLog markup; cursor-position
    sequences are dropped).
  - Footer: `↳ exit <N> · <duration_s>` (chip-colored green on 0, red
    nonzero, muted while running).
  - Latest command at the bottom. Click any block to copy it (same
    affordance as agent message blocks).
- **Status strip.** Bottom of the tab body: `cwd · pid · shell ·
  last_exit · subscribers: N`.
- **Input.** Single-line input at the bottom, same widget pattern agent
  tabs use. Enter synthesizes an `aegis_term_run` call (with
  `from_handle="human"`); the line text becomes the command. For raw
  input (interactive prompts, Ctrl-C), the input bar accepts a
  `Ctrl+K` modifier that switches it to raw mode: any keystroke is
  sent verbatim as `aegis_term_keys`. Modal indicator in the input bar
  shows the current mode.
- **Resume.** On `aegis --resume`, the tab body opens with the prior
  session's command log rendered with reduced opacity and a `--- end
  of previous session ---` rule below. Below the rule the new live
  shell starts fresh.

The existing queue dashboard (`Ctrl+D`) does not need to know about
terminals in v1. Terminal-level dashboards (per-cmd timings, exit
histograms) are future work.

### State layout

```
.aegis/state/terminals/<name>/
  meta.json         # {name, shell, cwd, env_at_spawn, started_at, version}
  ledger.jsonl      # one command record per line
  raw.log           # full raw PTY output (escape codes intact)
  init.sh           # the shell rcfile aegis wrote at spawn (bash variant)
  .zdotdir/         # ZDOTDIR for zsh variant, holding .zshrc
```

- `ledger.jsonl` is append-only. Each record:
  ```json
  {
    "seq": 0,
    "cmd": "pytest tests/",
    "writer": "agent:lucid-knuth",
    "started_at": "2026-05-22T14:03:21.123Z",
    "finished_at": "2026-05-22T14:03:25.341Z",
    "duration_s": 4.218,
    "exit": 0,
    "stdout": "...",
    "stderr": "...",
    "killed_by_restart": false,
    "timed_out": false
  }
  ```
- `raw.log` is the forward-compatibility hook. Future vt100 emulation
  replays from here. Capped at 10 MB with rotation (`raw.log.1`).
- `meta.json.version` is `1`. The state layout is intentionally
  daemon-friendly: a future `aegis-shelld` can adopt this directory
  as-is.

### Architecture

Three new modules under `src/aegis/terminal/`:

- **`parser.py`** — pure, no I/O. OSC 133 sequence detection and
  stripping. Given a byte chunk, yields `(stripped_chunk, [events])`
  where events are `PromptStart` / `CommandStart` / `CommandEnd(exit)`.
  Tested with synthetic byte streams, including split-across-chunks
  sequences and adversarial cases (markers inside string literals,
  multibyte UTF-8 split mid-sequence).

- **`pty.py`** — thin wrapper around `ptyprocess.PtyProcessUnicode`
  (already a transitive dep via Textual? if not, add as direct dep —
  pure Python). Owns the file descriptor pair, spawn, write, read,
  close. Provides an async stream interface.

- **`manager.py`** — `TerminalManager`. Public surface:
  - `async spawn(name, shell, cwd, env) -> TerminalInfo`
  - `async run(name, cmd, *, writer, timeout) -> CommandRecord`
  - `async send_keys(name, keys, *, writer) -> None`
  - `read(name, *, last_n, since_seq) -> list[CommandRecord]`
  - `subscribe(name, handle) -> list[str]`
  - `unsubscribe(name, handle) -> None`
  - `async close(name) -> None`
  - `list() -> list[TerminalInfo]`

  Per-terminal `_TerminalState` holds: `pty`, `asyncio.Lock` (held by
  `run`), `ledger_path`, `raw_log_fh`, `parser`, `subscribers:
  set[str]`, `current_command: CommandRecord | None`, and a notifier
  callback. The manager spawns a background reader task per terminal
  that pulls from the PTY, runs bytes through `parser`, writes raw
  bytes to `raw.log`, accumulates stdout/stderr into the current
  command record on `CommandStart`, and finalizes the record +
  appends to `ledger.jsonl` + invokes the notifier on `CommandEnd`.

The notifier is `Callable[[CommandRecord, _TerminalState], Awaitable[None]]`,
the same shape canvas uses. `notify.py` builds the inbox message and
calls `InboxRouter.deliver(handle, message)` for each subscriber except
the writer.

### Lock semantics

- `aegis_term_run` acquires the terminal's `asyncio.Lock`, writes the
  command (followed by `\n`) to the PTY, awaits the next `CommandEnd`
  event from the parser, finalizes and persists the command record,
  and releases the lock.
- The lock guarantees `run` calls serialize. While `run` is in flight,
  other `run` calls queue (FIFO, by asyncio.Lock fairness).
- `send_keys` does **not** take the lock. It writes verbatim to the
  PTY. If sent while a `run` is in flight, the bytes interleave with
  whatever the running command is reading from stdin — intentional, so
  agents and the human can answer interactive prompts during a
  long-running command.
- The TUI's typed-Enter command path uses `run` (so each typed line
  appears as a ledger record). The TUI's `Ctrl+K` raw-mode passes
  keystrokes through `send_keys`.

### Subscription wake — payload shape

`canvas/notify.py` is the precedent. `terminal/notify.py` builds:

```
✉ from term:<name> · <finished_at>
  $ <cmd>  · run by <writer>
  exit <N> · <duration_s>s
  ──
  <last 8 lines of stdout, trimmed>
  [if stderr non-empty:]
  ── stderr ──
  <last 4 lines of stderr, trimmed>
```

Configurable via constants in `notify.py` (`STDOUT_TAIL_LINES = 8`,
`STDERR_TAIL_LINES = 4`, `LINE_TRUNC = 200`). Writer suppression: the
subscriber whose handle equals `writer` does not receive a wake from
their own command. `writer="human"` wakes all subscribers.

`aegis_term_subscribe` is unfiltered in v1 — every command-finish
wakes every subscriber. Per-pattern filters (subscribe only to
commands matching a regex, or only to nonzero exits) are future work.

### Persistence and resume

`SessionManager`'s existing persistence layer (just shipped in
session-persistence-v1) gains a new section: `terminals: [{name,
shell, cwd, env_snapshot}, …]`. On `aegis --resume`:

1. SessionManager reads the saved terminal list.
2. For each, calls `TerminalManager.spawn(...)` with the saved shell +
   cwd + env. A new PTY is born; `meta.json` is updated with a new
   `started_at`.
3. The TUI tab body is populated with the prior session's `ledger.jsonl`
   contents (rendered with reduced opacity and a `--- end of previous
   session ---` separator), then the live new shell renders below.
4. Any command in the prior session whose `finished_at` is null (it was
   in-flight when aegis died) gets its record updated with
   `killed_by_restart: true` and `exit: null`. This is done by the
   manager during the first sweep of `ledger.jsonl` at spawn time —
   not in the persistence layer.

`aegis --clean` skips terminal restoration entirely. `raw.log` and
`ledger.jsonl` are never deleted by aegis (gitignored, manually
deletable).

Subscribers do not survive restart. An agent that was subscribed in the
prior session must re-subscribe (same as canvas — design precedent
intact).

### Failure modes and edge cases

- **PTY death mid-`run`.** Reader task sees EOF from the PTY. The
  in-flight `run` completes with `{exit: null, stderr: "pty closed",
  duration_s}` and the lock releases. Subsequent `run` calls error with
  "terminal closed". `list` no longer shows the terminal as live.
- **`aegis_term_close` while `run` is in flight.** Lock is acquired by
  `close`; SIGTERM is sent; the in-flight `run` sees PTY death and
  finalizes as above. Order: close-caller releases lock after the PTY
  is confirmed gone.
- **OSC 133 marker arrives mid-line inside command output.** The
  parser must handle the marker bytes appearing inside arbitrary
  output (e.g., a command that echoes the marker literally). The
  parser does not interpret markers inside the user's stdout — only
  bytes the shell itself emits. We rely on the shell snippet to emit
  markers only at prompt boundaries. A malicious / weird command that
  prints the literal escape sequence will cause a false event; this is
  an accepted limitation (matches iTerm/WezTerm behavior).
- **Long output.** `stdout` and `stderr` in the ledger record are
  capped at 64 KiB each. Excess is truncated with `[… <N> bytes
  truncated …]`. `raw.log` keeps the full bytes.
- **Concurrent spawn with same `name`.** Errors immediately;
  no race possible (the manager's spawn is itself locked on `name`).
- **Shell integration fails to install.** Detected on first prompt: if
  no `\e]133;A\a` is seen within 5s of spawn, the manager logs a
  warning and switches the terminal to fallback mode (marker
  injection). `meta.json` records `osc133: false`.

### Testing

Test pyramid, all hermetic (no real subshell spawns in unit tests):

**Parser (`tests/test_terminal_parser.py`)** — pure byte-level cases.
- OSC 133 A/B/D detection
- Stripping leaves output bytes intact
- Sequence split across two chunks
- Multibyte UTF-8 split mid-sequence
- D marker with multi-digit exit codes (e.g. 130)
- D marker missing exit code → treated as exit=null
- Adversarial: bytes resembling markers inside command output

**Manager (`tests/test_terminal_manager.py`)** — uses a fake PTY
fixture that exposes `write_to_master(bytes)` and `read_from_slave()`
without a real subshell. Drives the manager's reader task by feeding
synthetic byte sequences.
- Spawn → list shows entry
- `run` waits for `D` marker, returns correct exit
- `run` while another `run` is in flight serializes (FIFO)
- `send_keys` bypasses the lock and writes immediately
- Writer suppression on subscription wakes
- `read` with `last_n` and `since_seq`
- `close` cleans up state, terminates reader task
- Killed-by-restart flag set on stale in-flight records at spawn time
- Fallback mode kicks in if OSC 133 markers never appear

**Notify (`tests/test_terminal_notify.py`)** — uses an in-memory
`InboxRouter` stub.
- Payload shape matches spec (header, body, tails)
- Subscribers receive wakes; writer does not
- `human` writer wakes everyone
- Stdout/stderr tail trimming

**MCP (`tests/test_terminal_mcp.py`)** — uses FastMCP `call_tool`
in-process to exercise the 8 verbs end-to-end with a stub
TerminalManager.

**Smoke (manual, not CI)** — real bash + zsh on the dev machine:
- Spawn `build`, run `pytest tests/test_canvas_parser.py`, verify
  ledger record and TUI rendering.
- Subscribe from a second agent (in another tab), trigger `run`,
  verify `✉` block appears in subscriber's transcript.
- Send `\x03` via `aegis_term_keys` to interrupt a `sleep 60`; verify
  exit code 130 and ledger record.
- `aegis --clean` then `aegis --resume`: verify terminal restoration
  vs no-restoration.

### Implementation slices (vertical)

The implementation plan will use these as TDD slices; locking in here
so the plan stays honest:

1. **Slice 1: Parser.** OSC 133 byte-level parser. No PTY, no manager.
   All parser tests pass. Commit per test.
2. **Slice 2: Manager skeleton + spawn/close.** `TerminalManager` with
   `spawn`, `close`, `list`. Real PTY via `ptyprocess`. Tests with the
   fake-PTY fixture verify lifecycle. No `run` yet.
3. **Slice 3: `run` + lock.** Add `run` with the lock and command-end
   detection wired through the parser. Add `read`. Manager tests pass
   end-to-end with synthetic markers.
4. **Slice 4: Subscriptions + notify.** Wire `subscribe`/`unsubscribe`,
   build `terminal/notify.py`, plug into `InboxRouter`. Notify tests
   pass.
5. **Slice 5: MCP tools.** Eight new `@server.tool` registrations.
   `AppBridge` gains `terminal_manager`. Update `BRIEFING`. MCP tests
   pass.
6. **Slice 6: TUI tab type.** New `TerminalTab` widget, command-block
   rendering, status strip, input bar with `Ctrl+K` raw mode.
   Integrated visually; click-to-copy reuses existing affordance.
7. **Slice 7: Persistence.** Hook into `SessionManager` save/restore.
   Resume re-spawns terminals with saved cwd/shell and replays prior
   ledger cosmetically.
8. **Slice 8: Smoke + docs.** Manual smoke on bash + zsh; write
   `docs/terminals.md`; mention in README and landing page.

Each slice ends with a clean `uv run pytest` and a commit. The smoke
in slice 8 is the human verification step; everything before it is
hermetic.

### Forward-compatibility notes

- `meta.json.version: 1` lets a future `aegis-shelld` daemon detect
  the schema.
- `raw.log` preserves full PTY output bytes, so future pyte-based vt100
  rendering can replay any historical session.
- The `from_handle` argument is mandatory on subscribe/unsubscribe and
  optional on the others, matching the canvas/queue precedent. A future
  per-handle ACL layer can hook in here without surface changes.

## References

- [[2026-05-21-shared-canvas-design]] — direct architectural precedent
  for the ledger + notifier + inbox-wake pattern.
- [[2026-05-21-session-persistence-design]] — how `aegis --resume`
  hooks for non-agent state.
- OSC 133 shell integration spec: <https://gitlab.freedesktop.org/Per_Bothner/specifications/-/blob/master/proposals/semantic-prompts.md>
- iTerm2 shell integration script (reference implementation):
  <https://iterm2.com/documentation-shell-integration.html>
