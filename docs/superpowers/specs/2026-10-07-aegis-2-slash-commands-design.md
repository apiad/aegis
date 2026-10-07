# Slash commands in aegis 2

> **Status:** design, 2026-10-07, awaiting review. No plan yet.
> Issues: [#97](https://github.com/apiad/aegis/issues/97) (live model and effort).
> Earlier work: the TUI-era specs `2026-07-16-aegis-slash-commands-design.md` and
> `2026-07-17-aegis-slash-commands-2a..2d`, and the closed PR #123's
> `2026-10-04-aegis-live-model-and-effort-switch-design.md` (branch
> `docs/97-live-model-effort-spec`), whose measurements this spec reuses.

The composer of a session accepts lines that start with `/`. A menu completes
them as you type, and Alt+/ opens the same menu from anywhere in a session view.
Some commands are aegis's own (`/model`, `/effort`, `/permission`, `/rename`,
`/stop`, `/close`); everything else Claude Code knows (`/compact`, `/clear`,
`/context`, `/cost`, skills, `.claude/commands`) goes to the session's `claude`
as text. A line that names nothing known is refused before it costs a turn.

## What Claude Code does with a `/` line

Measured on zion on 2026-10-07 with Claude Code 2.1.283, driven the way aegis
drives it (`claude -p --input-format stream-json --output-format stream-json
--replay-user-messages --verbose`). Probes and raw JSONL are in the Workspace at
`.playground/aegis-slash/`; the #97 probes are in `.playground/model-switch/`.

### Text sent as a user message

| Sent | What came back |
|---|---|
| `/effort high`, `/model sonnet`, `/context`, `/cost`, `/mcp`, `/config` | **No user echo.** One `assistant` line with `"model": "<synthetic>"` and `"local_command_run": {"command": "effort", "args": "high"}`, its text the command's output (`Set effort level to high (this session only)…`), then a `result` with `num_turns: 0` and no cost. |
| `/model not-a-model` | The same shape, text `Model 'not-a-model' not found`. Nothing marks it as a failure. |
| `/hello world` (a `.claude/commands/hello.md`) | A replayed user line `<command-message>hello</command-message>\n<command-name>/hello</command-name>\n<command-args>world</command-args>`, then a normal paid turn. Skills behave the same. |
| `/compact` | `system/status compacting`, `system/compact_boundary` (pre and post tokens), a synthetic summary user line without `isReplay`, a replayed user line `<local-command-stdout>Compacted </local-command-stdout>`, and a `result`. No echo of `/compact` itself. |
| `/clear` | A top-level `{"type": "conversation_reset", "trigger": "clear", …}` line and a `result`. The next `init` carries a **new `session_id`**, so a later `--resume` continues the cleared conversation. |
| `/bogus-thing x` | Echoed as a plain prompt and answered by the model: a paid turn ($0.16 here) spent on a typo. |
| `/doctor` | A workspace skill of that name ran a long turn. Names are resolved against the catalog below, not against what Claude Code's terminal does. |
| `/effort max` and `/model haiku` sent while a turn ran tool calls | Not injected at a tool boundary the way a prompt is. Each waited for the turn's `result`, then ran as its own zero-cost turn with its own `result`. |

### Control requests

| Sent | What came back |
|---|---|
| `{"subtype": "initialize"}`, at start or after turns | `success` with `commands` (139 here: `name`, `description`, `argumentHint`, `builtin`), `models` (each with `value`, `resolvedModel`, `displayName`, `description`, `supportedEffortLevels`), `agents`, `current_permission_mode`. No API call, about 0.5 s on a cold process. |
| `{"subtype": "set_model", "model": "opus"}` | `success`; a replayed `<local-command-stdout>Set model to …` user line; the next `init` reports the new model. #97: a bad name gives `error` after about 5 s; mid-turn it applies from the next turn. |
| `{"subtype": "apply_flag_settings", "settings": {"effortLevel": "max"}}` | `success`; a following `get_settings` reports `applied.effort: "max"` (it was `low`). An unknown level also answers `success` and changes nothing (#97), so the read-back decides. |
| `{"subtype": "set_permission_mode", "mode": "acceptEdits"}` | `success` with `{"mode": "acceptEdits"}` and a `system/status` line carrying `permissionMode`. |
| `get_status` | Reports the model, not the effort. |

The `init` line also lists `terminal_slash_commands` (`doctor`, `color`,
`focus`, `reload-plugins` here): commands for the interactive terminal.

### What aegis does with these lines today

Folding the `/effort`, `/hello`, `/compact`, `/clear`, prompt sequence through
`transcript/entries.py` as it is:

- The prompts matched the wrong echoes. `/effort high` was resolved by the
  `<command-message>hello` echo, `/hello world` by `<local-command-stdout>Compacted`,
  and `/clear` stayed `pending` forever, because the fold pairs pending sends
  with echoes first in, first out and local commands are never echoed.
- `/hello world` showed as a user bubble of raw `<command-message>` tags, and
  `Compacted` as a user bubble.
- `/effort` output showed as agent prose.
- Nothing updated `SpawnSpec`, so the chips kept the old model and the next
  `--resume` started `claude` with the old `--model` and `--effort`, undoing the
  switch.

## Design

### Three kinds of `/` line

`session.send` keeps its params. When the text starts with `/`, the server
resolves it in a new module, `src/aegis/commands.py`, before anything is written
to `claude`:

1. **`//rest`** sends `/rest` as a plain prompt. The escape for a message that
   starts with a slash.
2. **An aegis command** runs the operation it stands for and sends nothing to
   `claude` as text. Each one is a line of syntax over an operation that already
   exists or that this spec adds, so the registry stays the only way to act
   (DESIGN.md, "One registry, every caller").
3. **A harness command**, a name in the session's catalog, goes to `claude`
   as typed. The fold handles what comes back (below).

Anything else fails with `unknown_command` ("no command /bogus-thing in this
session; start the line with // to send it as text"). The composer shows the
same error before Enter, from the catalog it already holds.

Aegis commands win over harness commands of the same name. That shadows
Claude's own `/model`, `/effort` and `/rename`, so the chips, the meta and the
next resume always agree with the process.

| Command | Operation | Effect |
|---|---|---|
| `/model [name]` | `session.configure` | switch the model; with no name, open the menu on the models |
| `/effort [level]` | `session.configure` | switch the effort; levels are the current model's `supportedEffortLevels` |
| `/permission [mode]` | `session.configure` | `read`, `write`, `auto` or `full`, the spawn form's vocabulary |
| `/rename <handle>` | `session.rename` | |
| `/title <text>` | `session.rename` | |
| `/stop` | `session.stop` | |
| `/close` | `session.close` | asks first, like the button |

`/help` is a client action: it opens the menu. Nothing else is client-only.

### `session.configure`: one operation for model, effort and permission

```python
class ConfigureParams(_Strict):
    log_id: str
    model: str | None = None
    effort: Effort | None = None       # widened to the CLI's levels, xhigh included
    permission: Permission | None = None
```

1. Validate against the session's catalog: the model must be a `value` or a
   `resolvedModel` in `models` (entries with `disabled: true` refused); the
   effort must be in that model's `supportedEffortLevels`, and a model with no
   levels (Haiku 4.5) refuses any effort with "<model> takes no effort level".
2. If the session has a process, apply it through control requests and wait for
   each answer, bounded at 15 s: `set_model`; `apply_flag_settings` then
   `get_settings`, `ok` only if `applied.effort` equals the level asked;
   `set_permission_mode`. A refusal reaches the person as the operation's error,
   and the spec is left unchanged.
3. Replace `session.spec` (it is a frozen dataclass, so `dataclasses.replace`),
   write the meta, and record `{"kind": "configure", "model": …, "effort": …,
   "permission": …}` in the store. The fold turns it into one system entry,
   `model → claude-sonnet-5`, with "from the next turn" while a turn is running
   and "when it resumes" on a stopped session. `meta.rebuild` applies
   `configure` records after the `spawn` record, so a rebuilt meta agrees.

A stopped session needs no process for any of this: step 2 is skipped and the
next `ensure_running` passes the new `--model`, `--effort` and
`--permission-mode`. That also fixes the resume half of #97.

`session.configure` is a person's operation. Opening it to agents ("change your
own model") is a separate decision.

### Talking to `claude` with replies: `ClaudeProcess.request`

`ClaudeProcess` gains `async request(subtype, **fields) -> dict`. It writes a
`control_request` with a fresh `request_id`, parks a future, and `_pump_stdout`
resolves it when a `control_response` with that id arrives, before the line
reaches `on_line`. A response with `subtype: "error"` raises `ControlError`
with its message; no answer in 15 s raises `TimeoutError`. The interrupt keeps
writing without waiting, as now.

Answered control responses are not stored. They carry no transcript entry, and
`initialize` alone is about 60 KB, which every process start would otherwise add
to the store.

The Claude-specific part (which subtypes, the read-back for effort, how a
catalog is read) lives in `src/aegis/claude/control.py`, so a second harness
adds its own module with the same three functions: `catalog()`,
`set_option(kind, value)`, and the names of the commands it runs locally. #97
measured OpenCode's: `session/set_config_option` by `category` (`model`,
`thought_level`, `mode`), with the levels changing per model. Building it waits
for an OpenCode harness in aegis 2.

### The catalog: what the menu lists

`commands.list {log_id}` returns:

```json
{
  "commands": [
    {"name": "model", "hint": "[name]", "doc": "Switch the model", "source": "aegis", "args": "models"},
    {"name": "compact", "hint": "", "doc": "Clear history but keep a summary…", "source": "claude"},
    {"name": "superpowers:brainstorming", "hint": "", "doc": "You MUST use this before…", "source": "plugin"},
    {"name": "draft", "hint": "<outline-path>", "doc": "Generate a voice-calibrated draft…", "source": "project"}
  ],
  "models": [{"value": "sonnet", "label": "Sonnet 5", "doc": "…", "efforts": ["low", "medium", "high", "xhigh", "max"]}],
  "permissions": ["read", "write", "auto", "full"],
  "current": {"model": "sonnet", "effort": "high", "permission": "auto"},
  "complete": true
}
```

- **Where the harness part comes from.** Each process start sends `initialize`
  and keeps the answer in memory, keyed by the session's cwd, since commands come
  from the cwd's `.claude/` and the user's config. A session with no process
  uses the answer for its cwd. If there is none (the server restarted and
  nothing in that cwd has run since), the server starts `claude` once with the
  session's flags, sends `initialize`, takes the answer and ends it: about 0.5 s
  and no tokens. Nothing goes to disk, so an upgraded CLI never meets a stale
  list. #97 rejected a disk cache for the same reason.
- **What is dropped.** `terminal_slash_commands`, Claude's own entries that an
  aegis command shadows, and models marked `disabled`.
- **`source`.** `aegis`, `claude` (`builtin: true`), and for the rest the
  parenthesised suffix Claude puts on the description, `project`, `user` or a
  plugin's name, else `skill`. Here: 54 builtin, 49 project, 16 synced from
  claude.ai, 3 user, 16 other.
- **Size.** Descriptions are cut to their first sentence and 140 characters:
  61 KB becomes 28 KB for 139 commands. The client asks once per focused session
  and again when a `configure` changes the model.
- **Freshness.** Every process start refreshes the cwd's entry, so a new skill
  shows up after the next resume. A `system/commands_changed` line, which the CLI
  can emit, also triggers a refresh.

Plugins will add commands to the same list with `source` set to the plugin; the
vision gives plugins slash commands, and this list is where they appear.

### The menu in the browser

One component, `client/js/commands.js`: a list drawn above the composer, at
most eight rows, each with the name, the argument hint in a dim face, the first
line of the description and a source tag. Matching is the legacy fuzzy scorer
ported to JS (case-insensitive subsequence; +2 per contiguous character, +3 at
a word start, −0.01 per character of length), with aegis commands first on a
tie. It runs in the browser on every keystroke against the list it fetched;
going to the server per key would add a round trip through a proxy like
dev.apiad.net.

- **Opening it.** Typing `/` as the first character of the composer, or Alt+/
  anywhere in a session view, which focuses the composer and opens the menu. If
  the composer holds a draft that is not a `/` line, Alt+/ opens the menu with
  its own one-line filter above the rows and leaves the draft alone; running a
  command from there goes through the same `session.send`.
- **Arguments.** After `/model ` the rows are the models (label, description,
  whether it takes effort); after `/effort ` the current model's levels; after
  `/permission ` the four modes. A harness command shows its `argumentHint` and
  completes nothing.
- **Keys.** Up and Down move. Tab puts the highlighted row into the line. Enter
  does the same while the highlighted row would change the line, and sends when
  the line is already a whole command. Esc closes the menu and does not
  interrupt the agent; a second Esc interrupts, as now.
- **Before sending.** A `/` line whose name is not in the list turns the
  composer's outline to the error colour with "unknown command; // sends it as
  text".
- **The chips.** Clicking the model, effort or permission chip opens the menu on
  `/model `, `/effort ` or `/permission `.

The menu draws nothing about a command's meaning: names, hints, descriptions and
sources all come from `commands.list`.

### The transcript: folding what a harness command returns

The fold keeps two pending queues instead of one: prompts and commands. A sent
text that starts with `/` (after the server resolved it as a harness command)
joins the command queue. Then:

| Line | Entry |
|---|---|
| assistant, `model: "<synthetic>"`, `local_command_run` | resolves the oldest pending command; one `command` entry titled `/effort high`, its `md` the output with the `<local-command-stdout>` wrapper removed |
| replayed user `<command-message>…<command-name>/x</command-name><command-args>a</command-args>` | resolves the oldest pending command; a `user` entry drawn as `/x a`, followed by the turn as usual |
| replayed user `<local-command-stdout>…</local-command-stdout>` | resolves the oldest pending command if one waits (as for `/compact`), else adds the output to the last command entry; never a user bubble |
| `conversation_reset` with `trigger: "clear"` | resolves the pending `/clear`; a system entry "context cleared; Claude started a new conversation" |
| any other replayed user line | resolves the oldest pending prompt, as now |

Two queues because a prompt sent mid-turn is read at the next tool boundary
while a command waits for the turn to end, so the two kinds are answered out of
order. A new glyph in `describe.py` marks command entries; `⌬` is taken by
Bash.

`claude/stream.py` gains the events behind these rows: `LocalCommand`
(`command`, `args`, `text`), `CommandEcho` (`name`, `args`), `CommandOutput`
(`text`) and `Reset` (`trigger`). `Init` already updates `claude_session_id`,
which is what makes `/clear` survive a resume.

### Status, queues and the inbox

A harness command moves the session to `working` like any send, and its
zero-cost `result` moves it back, ending the turn: held inbox messages flush and
a queue worker may be judged finished. That is right for `/compact` and
`/clear`, which do end a turn. `/model`, `/effort` and `/permission` never touch
the turn state, because they are control requests.

## Testing

- `tests/fake_claude.py` answers `initialize`, `set_model`,
  `apply_flag_settings`, `get_settings` and `set_permission_mode`, and replays the
  line shapes above for `/effort`, `/hello`, `/compact`, `/clear`, copied from
  the recorded probes rather than written by hand.
- Fold tests replay those recordings and check: no entry left pending, one
  `command` entry per local command, `/hello world` drawn as itself, the
  patches equal a fresh fold (the existing invariant).
- Session tests: `/model sonnet` on a live session sends `set_model` and the
  chip changes; on a stopped one nothing is spawned and the next spawn's argv
  carries `--model sonnet`; a bad effort is refused before any write; an
  `applied.effort` that does not match fails the operation and leaves the spec.
- Browser tests: `/mo`, Tab, `son`, Enter switches the model; Alt+/ with a
  draft keeps the draft; Esc closes the menu without interrupting; an unknown
  name shows the error and sends nothing.
- `make test-live` with a real `claude`: `/model`, `/effort`, `/compact`,
  `/clear`, then stop and resume and check the model in `init`.

## Out of scope

- Commands in the Fleet view (`/new`, go to a session). Alt+/ there does nothing
  yet.
- The legacy TUI's other commands: `/spawn`, `/fork`, `/loop`, `/btw`,
  `/peer` and `@handle`, `/queues`, `/enqueue`. Each gets an issue if wanted.
- `.aegis/commands/*.md` prompt files. Claude Code already runs
  `.claude/commands` and skills, and passing them through keeps one place for
  them.
- `session.configure` for agents.
- OpenCode and other harnesses: the module boundary is here, the code waits for
  the harness.
