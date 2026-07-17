# Slash commands 2B — Full builtin coverage — design spec

**Date:** 2026-07-17
**Status:** Approved — ready for implementation plan
**Owner:** Alex + Claude
**Builds on:** `2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md` (2A, shipped)

## Summary

2A gave slash commands a declarative typed-arg layer (`ArgSpec`/`Args`),
a protected-builtin registry, `//` escaping, `/queue new` persistence, and
web-input parity. It shipped six builtins (`/help /sessions /agents /spawn
/queue /enqueue`).

2B exposes the rest of the operator-useful `AppBridge` surface as builtin
commands, so the meta-harness can be driven from the keyboard without an
agent round-trip: coordination (`/handoff`, `/group`, `/schedule`), session
control (`/rename`, `/close`, `/theme`, `/clear`), terminals (`/term`), and
agent management (folded into `/agents`). Every command is a thin call over
an existing bridge attribute, a `config.edit` helper, or the small new
**effect channel** (for the two frontend-mutating commands). Web parity is
threaded through: result-block commands work in the web input box for free
(shared `dispatch()`); the two effect commands get matching `app.js` code.

## Scope decisions (settled in brainstorming)

- **Operator-useful subset, not full MCP parity.** Read/status + lightweight
  lifecycle verbs only. Heavy orchestration (group `broadcast`/`wait_*`/
  `spawn_mixed`, `schedule push` with a spec dict) stays MCP-only — that is
  agent territory, not a thumb at the keyboard.
- **`/model` and `/effort` are DEFERRED** to a follow-up "session-mutation"
  slice (2B.1). They are the only commands that can't be a thin bridge call:
  claude bakes `--model`/`--effort` into the subprocess argv at spawn
  (`ClaudeDriver.build_argv`/`session`), and the subprocess is long-lived
  across turns, so changing them mid-session is a resume-restart
  (tear down + `resume()` with new argv, conversation preserved via
  `session_id`) — driver-capability-dependent session surgery that deserves
  its own focused TDD pass. Not in 2B.
- **No standalone `/config` command.** Agent management folds into `/agents`
  (the command that already lists agents). Queue creation already lives in
  `/queue new`. Raw-config viewing is not operator-essential; the
  noun-commands surface everything.
- **Convention: a bare noun-command is equivalent to its `list`.** `/agents`,
  `/sessions` (already), `/group`, `/schedule`, `/term`, `/theme`, and
  (newly) `/queue` all list when invoked with no subverb. Uniform.

## Design

### 1. The command set

Subverb-dispatched commands take an **optional** first positional
(`subverb`); the handler branches on it, treating a missing/`list` subverb as
the list view (the bare-command convention). Additional positionals are
optional and validated inside the handler per subverb (the 2A parser has no
sub-parsers; this mirrors how `/queue new` already works).

**Coordination**

| Command | Bridge / helper call |
|---|---|
| `/handoff <target> <context…>` | `await bridge.handoff(ctx.handle, target, context)` — `context` greedy/verbatim |
| `/group` \| `/group list` | new `bridge.groups` list method (§3) → `name · N members` per group |
| `/group status <name>` | `await bridge.groups.status(name)` |
| `/group dissolve <name>` | `await bridge.groups.dissolve(name)` |
| `/schedule` \| `/schedule list` | `list_payload(bridge.scheduler, bridge.state_root, bridge.inline_schedule_names())` |
| `/schedule show <name>` | `show_payload(bridge.scheduler, bridge.state_root, bridge.inline_schedule_names(), name)` |
| `/schedule enable <name>` \| `/schedule disable <name>` | `config.edit.set_schedule_enabled(root, name, value)` |
| `/schedule remove <name>` | `remove_schedule(bridge.scheduler, bridge.state_root, bridge.inline_schedule_names(), name)` |
| `/schedule logs <name>` | `logs_payload(bridge.state_root, name)` |

(`list_payload`/`show_payload`/`remove_schedule`/`logs_payload` are the same
`aegis.scheduler.push` helpers the MCP `aegis_schedule_*` tools call.)

**Session control**

| Command | Behavior |
|---|---|
| `/rename <new>` | `await bridge.rename_handle(ctx.handle, new)` — renames the current pane |
| `/close [handle]` | `await bridge.close(handle or ctx.handle)` — defaults to the current pane |
| `/theme` \| `/theme list` | list available theme names (§3) in the result body |
| `/theme <name>` | result `ok`, `effect={"kind": "theme", "name": <name>}` |
| `/clear` | result `ok`, `effect={"kind": "clear"}` — cosmetic (§2) |

**Terminals** (over `bridge.terminal_manager`)

| Command | Manager call |
|---|---|
| `/term` \| `/term list` | `terminal_manager.list()` → `name · pid · shell` |
| `/term new <name>` | `await terminal_manager.spawn(name, from_handle=ctx.handle)` |
| `/term run <name> <cmd…>` | `await terminal_manager.run(name, cmd, writer=ctx.handle)` — blocks until the command finishes; returns its `stdout`/`exit` as the result block (matches `aegis_term_run`) |
| `/term close <name>` | `await terminal_manager.close(name)` |

**Agents** (extend the existing `/agents`)

| Command | Behavior |
|---|---|
| `/agents` \| `/agents list` | existing 2A list (unchanged) |
| `/agents add <slug> <harness> <model> [--effort E] [--permission P]` | `config.edit.add_agent(root, slug, harness=…, model=…, effort=…, permission=…)` then `bridge.register_agent(slug, fresh)` |
| `/agents remove <slug>` | `config.edit.remove_agent(root, slug)` (persisted; live drop needs restart — reported in the result body) |

**Queues** (extend the existing `/queue` for the bare-list convention)

| Command | Behavior |
|---|---|
| `/queue` \| `/queue list` | list configured queues (`name · agent · max_parallel`) — new bare-list branch |
| `/queue new <name> [agent] [--ephemeral]` | unchanged from 2A |

### 2. `/clear` semantics — cosmetic + honesty marker

`/clear` wipes the **visible transcript scrollback only**; the agent's
conversation context is untouched. To avoid the illusion that context was
reset, the clear leaves a **persistent marker** in the now-empty transcript
reading, e.g.:

```
──── transcript cleared · 47.2k context tokens still in play ────
```

The token count is the pane's live context size — `SessionMetrics
.last_true_input` (the field the status-line % gauge already uses). It is
rendered **frontend-side** (the frontend owns the metrics), so the core
effect payload is just `{"kind": "clear"}`; the seam supplies the number.

True context-reset (drop `session_id`, start fresh) is session surgery and
rides with the deferred 2B.1 session-mutation slice, not here.

### 3. New code beyond thin calls

Everything above is an existing bridge attr / `config.edit` helper / effect,
**except** two small additions:

1. **`/group list` needs a group-listing method.** `bridge.groups`
   (`_GroupsBridge` + the `GroupsBridge` Protocol in `groups/bridge.py`)
   today exposes `status`/`dissolve`/`rename`/`move_member`/`spawn`/… but no
   "list all groups". Add one method — `list_groups() -> list[dict]` (or
   `names()`), implemented off the existing `registry.names()` +
   per-group member counts. Added to both the Protocol and the concrete
   `_GroupsBridge`; the `make_groups_bridge` factory already closes over the
   registry.

2. **`/theme` name list must be harness-agnostic.** `aegis.tui.themes`
   imports Textual (`Theme`), so the commands core cannot import it. Expose
   the small, stable set of theme names (`ink`/`parchment`/`slate`, i.e. the
   `aegis-ink`/`aegis-parchment`/`aegis-slate` Textual theme ids) as a plain
   constant in a Textual-free module that both `themes.py` and the command
   import. (Exact home resolved in planning — likely a `THEME_NAMES` tuple
   beside `load_theme`.)

No other new plumbing. `/model`/`/effort` (the resume-restart method) are out
of scope per §Scope.

### 4. The effect channel

Add one optional field to the frozen `CommandResult`:

```python
@dataclass(frozen=True)
class CommandResult:
    ok: bool
    title: str
    body: str = ""
    effect: dict | None = None      # frontend-applied side-effect, or None
```

The pure core only **declares** the effect; each frontend seam **applies**
the effect it recognizes *after* mounting the result block. Unknown effect
kinds are ignored (forward-compatible for 2C/2D).

- **TUI** (`tui/pane.py`, in the `/`-branch of `on_growing_input_submitted`,
  after `_mount_block`): `kind == "theme"` → `self.app.theme = <textual id>`;
  `kind == "clear"` → clear the transcript widget's children, then mount the
  honesty marker built from this pane's `SessionMetrics.last_true_input`.
- **Web** (`web/static/js/app.js`): the `deliver` response's
  `command_result` gains `effect`; `mountCommandBlock` (or the deliver
  handler) applies `theme` via the existing `applyTheme` + `localStorage`
  path, and `clear` by emptying the tab's transcript DOM and inserting the
  same marker built from the tab's metrics.
- **Web seam** (`web/wssession.py`, `_deliver_or_command`): include `effect`
  in the `command_result` frame:
  `{"ok", "title", "body", "effect"}`.

This is the one new core concept (~10 lines) and is reused by 2C/2D.

### 5. Module split

`builtins.py` (165 lines, 6 commands) becomes a `builtins/` package so no
single file balloons; each submodule registers its commands on import:

- `builtins/__init__.py` — imports every submodule so registration
  side-effects fire (keeps `from aegis.commands import builtins` working, as
  the bottom-of-`__init__.py` import in the commands package expects).
- `builtins/core.py` — the 2A six moved verbatim (`help`, `sessions`,
  `agents`, `spawn`, `queue`, `enqueue`) **plus** the `/agents` add/remove
  branches and the `/queue` bare-list branch (they extend existing commands).
- `builtins/coordination.py` — `handoff`, `group`, `schedule`.
- `builtins/session_ctl.py` — `rename`, `close`, `theme`, `clear`.
- `builtins/terminals.py` — `term`.

## Component boundaries

- Commands core (`commands/`) stays **harness-agnostic** — no Textual/web
  imports. `CommandResult.effect` is a plain dict; the frontends interpret
  it. `/theme` sources names from a Textual-free constant.
- `builtins/*` — concrete commands; depend on the registry + `args` + the
  bridge protocol + `config.edit` + `scheduler.push` helpers.
- Seams (`tui/pane.py`, `web/wssession.py` + `app.js`) — the only
  Textual/web-aware code; both delegate to the pure core and apply effects.
- Groups bridge gains one read method; the theme-name constant is the only
  new shared symbol.

## Testing

Hermetic (`-m "not live"`), TDD — failing test first per unit. Extend the
`FakeBridge` in `tests/test_slash_commands.py` to record/serve the new
surface (`handoff`, `close`, `rename_handle`, fake `groups` with
`list_groups`/`status`/`dissolve`, fake `terminal_manager`, `scheduler` +
`state_root` + `inline_schedule_names`).

- **Coordination** — `/handoff a hello world` calls `bridge.handoff("me",
  "a", "hello world")` (greedy context verbatim); bare `/group` lists;
  `/group dissolve g` calls `dissolve`; `/schedule list` returns the payload;
  `/schedule enable s` calls `set_schedule_enabled(root, "s", True)`.
- **Session control** — `/rename new` calls `rename_handle("me", "new")`;
  `/close` closes `ctx.handle`, `/close other` closes `other`; `/theme dark`
  returns `effect={"kind":"theme","name":…}`; `/clear` returns
  `effect={"kind":"clear"}`.
- **Terminals** — bare `/term` lists; `/term new t` spawns; `/term run t ls`
  runs and surfaces output; `/term close t` closes.
- **Agents / queues** — `/agents add r claude sonnet` persists +
  hot-registers; `/agents remove r` persists; bare `/agents` still lists;
  bare `/queue` lists; `/queue new …` unchanged.
- **Effect channel** — `CommandResult.effect` defaults `None`; a result with
  an effect round-trips through `dispatch`.
- **TUI seam** (`tests/test_pane_slash_command.py`) — `/theme <name>` mutates
  `app.theme`; `/clear` empties the transcript and mounts the marker
  (flaky-aware: re-run alone per AGENTS.md before believing an inotify
  failure).
- **Web seam** (`tests/test_web_slash.py`) — a subset command (`/group`)
  returns a `command_result` and does not call `core.deliver`; the frame
  carries `effect` for `/theme`/`/clear`.
- **Group bridge** — `list_groups()` returns the expected shape off a
  registry with two groups.

`app.js` is not unit-tested (no JS harness); the theme/clear effects get a
manual browser smoke in the verification task.

## Estimate

Bigger than 2A but well under a day at our pace: many thin commands, one
effect-channel field, one small groups-bridge method, one theme-name
constant, and the module split. Implemented in grouped commits
(module-split + effect-channel scaffold → coordination → session-ctl →
terminals → agents/queue extensions → web/TUI seam effects → verification),
TDD each.
