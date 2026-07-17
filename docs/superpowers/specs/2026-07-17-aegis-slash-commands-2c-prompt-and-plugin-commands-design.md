# Slash commands 2C — Prompt commands + plugin `@command` — design spec

**Date:** 2026-07-17
**Status:** Approved — ready for implementation plan
**Owner:** Alex + Claude
**Builds on:** 2A (parser + `source`-tagged registry + `CommandCollision` +
`CommandResult.effect`), 2B (full builtin coverage), 2D (command palette +
`Arg.completer` + `Completion`/`Completions`). All three shipped to `main`.

## Summary

2A/2B/2D built the slash-command *machine*: a typed-arg parser, a
`source`-tagged registry with a protected-builtin collision guard, a broad
builtin set, and an introspecting palette. Every command so far is a
**builtin** — registered in code, `source="builtin"`. 2C adds the two remaining
command **sources** the whole system was designed around:

1. **Prompt commands** — user-authored `.aegis/commands/<name>.md` files
   (frontmatter + a body template). On invocation the template is expanded and
   **sent to the agent as a normal user message** — Claude-Code parity. Not a
   control command: it produces no result block, it drives a turn.
   `source="user"`.
2. **Plugin `@command`** — a fourth plugin primitive beside
   `@workflow`/`@hook`/`@tool`. A plugin command is a **control command**
   (returns a `CommandResult`, like a builtin) contributed by an installed
   plugin, auto-registered on the existing plugin-import sweep.
   `source="plugin"`.

Both plug into 2A's registry unchanged: they produce ordinary `SlashCommand`s
with a `source` tag, so `dispatch()`, `/help` grouping, and the 2D palette pick
them up for free. Web parity is threaded through the one slice that adds a
new terminal behavior (delivering an expansion to the agent), as in 2A/2B/2D.

## Motivation

Phase 1 and 2A/2B/2D deliberately shipped *only* builtins while building the
seams — the `source` field, the `CommandCollision` guard, the `Arg.completer`
introspection — explicitly so 2C could add user and plugin sources with no
rework. This spec spends those seams. Prompt commands give the operator
Claude-Code-style reusable prompt templates (`/review`, `/plan`, `/standup`)
that live in the project and expand with arguments, file includes, and shell
output. Plugin `@command` lets a shipped plugin contribute an operator-facing
control command (a fourth extension point) the same way it already contributes
workflows, hooks, and tools.

## The key design fork (resolved)

Builtin/plugin **control commands** return a `CommandResult` rendered as a
block in the transcript. Prompt commands **expand to text delivered to the
agent as a user message** — a different terminal behavior. The seam must tell
them apart.

**Decision: ride the existing `CommandResult.effect` channel.** A prompt
command's handler returns
`CommandResult(ok=True, title="/<name>", effect={"kind": "deliver", "text": <expanded>})`.
Both dispatch seams already interpret `effect` (2B's theme/clear). They gain
one case: a `deliver` effect routes `effect["text"]` through the **normal
agent-deliver path** (mount a user line + `core.deliver`) *instead of* mounting
a command-result block. `dispatch()` stays pure and source-agnostic; the
distinction lives entirely in the two seams that already branch on `effect`.

Rejected alternative: a `kind: control|prompt` field on `SlashCommand` with a
parallel branch in each seam. It spreads the distinction into more places for
no gain — the `effect` channel already exists for exactly "a structured
side-effect the frontend interprets."

## Design

### 1. Registry precedence — edit `commands/__init__.py`

2A's `register()` protects only builtins (a non-builtin cannot shadow a
builtin). With two non-builtin sources now, define a strict source precedence:

```
builtin (rank 0)  >  user (rank 1)  >  plugin (rank 2)
```

`register(cmd)` rule, order-independent:

- No existing command of that name → register.
- Existing is **same source location re-registering** (same file for a prompt
  command, same `co_filename`/`co_firstlineno` for a `@command`) → idempotent
  replace, mirroring `@workflow`'s reload behavior.
- Incoming rank **strictly better** (lower number) than existing → replace
  (a user `.md` overrides an already-registered plugin command).
- Incoming rank **equal** → keep the first, raise `CommandCollision` (two
  `.md` files or two plugin commands with the same name; loaders sort inputs so
  "first" is deterministic).
- Incoming rank **worse** → raise `CommandCollision` (a plugin command cannot
  shadow a user `.md`; a non-builtin cannot shadow a builtin — the 2A case,
  preserved).

Both loaders call `register()` inside try/except and log a `CommandCollision`
as a load-time warning; the winner is deterministic and independent of which
loader runs first.

**Rationale for user > plugin:** a project-local `.aegis/commands/*.md` is the
most specific, explicit intent — "my config beats installed code" — the same
way inline `.aegis.yaml` entries and local overlays win.

### 2. Template expansion — new module `commands/expand.py`

One pure-ish function:

```python
def expand(template: str, argstr: str, root: Path,
           run_shell: ShellRunner) -> str
```

`run_shell` is injected (the seam passes a bound `run_shell_escape`) so the
module stays free of TUI imports and is unit-testable with a fake runner.
Expansion is **args-first**, Claude-Code order:

1. **Argument substitution** (from the raw `argstr`):
   - `$ARGUMENTS` → the raw stripped remainder verbatim.
   - `$1`..`$9` → the nth `shlex`-split token (missing → empty string).
2. **`@file` includes** — a `@<path>` token splices the file's text contents
   inline. Path resolves relative to `root` (the project root). A missing or
   unreadable file raises `ExpandError` (surfaced by the command as an error
   result — better than silently emitting nothing).
3. **`` !`cmd` `` embeds** — an `` !`...` `` segment runs the command via the
   injected `run_shell` (in `root`) and inlines its stdout.

Args substitute **before** includes/shell are processed, so `` !`git log $1` ``
works (CC parity): the `@file`/`` !`…` `` scan runs over the already-substituted
text, so an argument value *can* influence an include path or a shell command.
That is the accepted consequence of the trust boundary (both the command file
and the invocation are Alex's; see Security), not a defect. `ExpandError(ValueError)`
carries a human-facing message.

### 3. Prompt-command loader — new module `commands/prompt_loader.py`

```python
def load_prompt_commands(root: Path, run_shell: ShellRunner) -> list[str]
```

- Scans `<root>/.aegis/commands/*.md` (top level only in v1; sorted for
  deterministic order). Directory absent → no-op.
- Each file → a `SlashCommand`:
  - `name` = file stem (lowercased).
  - `summary` = frontmatter `description` (or "" ).
  - `usage` = `"/<name> " + argument-hint` when frontmatter `argument-hint` is
    present, else `"/<name>"`.
  - `source = "user"`.
  - `spec = ArgSpec(positionals=(Arg("arguments", required=False, greedy=True),))`
    — a single greedy positional, so `dispatch()` hands the handler the raw
    rest-of-line and the palette (2D) treats it as free text (greedy → no value
    completion; the `argument-hint` shows as the ghost usage).
  - `run` = a closure capturing the body template + `root` + `run_shell`. On
    invocation it calls `expand(template, args["arguments"] or "", root,
    run_shell)` and returns
    `CommandResult(ok=True, title=f"/{name}", effect={"kind":"deliver","text": expanded})`.
    An `ExpandError` is caught and returned as `CommandResult(ok=False, …)`.
- Frontmatter is a leading `---`-fenced YAML head (parsed with the YAML lib
  aegis already depends on; the plan grounds whether a small split helper
  already exists or one is added). No frontmatter → empty `summary`/default
  `usage`; no body → empty template (valid, expands to `""`, warning logged).
- Returns the list of registered names (for logging / tests). Collisions are
  caught and warned per §1.

### 4. Plugin `@command` decorator — new module `commands/decorator.py`

The fourth primitive, same lifecycle as `@workflow`/`@hook`/`@tool`
(auto-imported by `yaml_loader.import_plugins()`; no new loader).

```python
@command                                   # bare
@command(name=..., summary=..., usage=..., spec=ArgSpec(...))
async def h(ctx, args) -> CommandResult: ...
```

- Handler signature is the existing `async def h(ctx: CommandContext, args:
  Args) -> CommandResult` — validated as a coroutine function whose first two
  params are `ctx, args` (parallel to `@workflow`'s `engine`-first check).
- Defaults: `name` = function name; `summary` = first line of the docstring (or
  ""); `usage` = `"/<name>"` plus a spec-derived positional hint when the spec
  is non-empty; `spec` = `ArgSpec()`.
- Builds `SlashCommand(..., source="plugin")` and calls `register()` — so 2A's
  precedence guard (§1) applies: a plugin command that collides with a builtin
  or a user `.md` raises `CommandCollision`, which the plugin import surfaces
  fail-loud (consistent with how `import_plugins` treats workflow/tool import
  errors).
- Exposed from `aegis.commands` (`from aegis.commands import command`) so plugin
  authors import it beside `workflow`/`hook`/`tool`.

### 5. Boot wiring — edit `cli.py`

`import_plugins()` is called at each boot entry (`aegis`, `aegis serve`, `aegis
web`, and the `workflow` path). Beside each call, invoke
`load_prompt_commands(root, run_shell)` with the same project root
`import_plugins` uses. Boot-load only — **no live filesystem watch** in v1.
Editing or adding a `.aegis/commands/*.md` (or a new `@command`) takes effect on
the next `aegis`/`aegis serve` start, exactly like adding a new `@workflow`
today. (Wiring `.aegis/commands/` into the scheduler `ReloadWatcher` for live
editing is a clean follow-up; deferred to keep v1 tight and avoid coupling
command-loading into the scheduler watcher.)

The `run_shell` passed to the loader is the harness-agnostic
`run_shell_escape`-shaped callable already used by the `!` prefix; at the boot
seam it is bound to the project root so prompt-command `!`…`` embeds run there.

### 6. Deliver-effect seam — edit `tui/pane.py` + `web/wssession.py`

Both seams already call `dispatch()` and apply `result.effect`. Add one case to
each: when `result.effect` is a `deliver` effect
(`result.effect.get("kind") == "deliver"`), do **not** mount a command-result
block; instead take `result.effect["text"]` and run it through the existing
normal-message path:

- **TUI** (`on_growing_input_submitted`): set `text = effect["text"]` and fall
  through to the existing `InboxMessage` + `core.deliver` flow (mounts a user
  line, queues-or-lands as any typed message would). The expansion becomes an
  ordinary turn — chip-queuing, interrupt-send, and history recall all apply
  unchanged.
- **Web** (`_deliver_or_command`): when the dispatch result carries a `deliver`
  effect, call `core.deliver` with the expanded text and return the usual
  `{delivery, depth}` frame (not a `command_result` frame) so the web client
  renders it as a normal user message.

An error `CommandResult` from a prompt command (e.g. missing `@file`) has no
`deliver` effect, so it renders as a command-error block through the existing
path — the operator sees why expansion failed and nothing reaches the agent.

### 7. Palette source-coloring — edit `commands/__init__.py` + both frontends

2D's palette lists all sources but renders them uniformly. Add source
provenance so the panel tints by origin:

- `Completion` gains `source: str = "builtin"`.
- `complete()` fills `source=c.source` when emitting command-name completions
  (the verb-in-progress branch); argument-value completions keep the default.
- **TUI** `CommandPalette` tints each row's label by `source`
  (builtin = accent, user = `$success`/green, plugin = `$secondary`/purple),
  reusing the theme role palette already threaded through the widget.
- **Web** drop-up adds a `source` class per row; `app.css`/inline styles tint to
  match.

This is a small additive touch on 2D's already-shipped surface — the completion
engine and keys are unchanged.

## Component boundaries

- `commands/expand.py` — pure template expansion. Injected `run_shell` + `root`;
  no registry, no bridge, no UI. `ExpandError` out on bad include/shell.
- `commands/prompt_loader.py` — filesystem → `SlashCommand`s. Depends on
  `expand`, the registry, `args`, and frontmatter parsing. No UI.
- `commands/decorator.py` — `@command` → `SlashCommand(source="plugin")` via
  `register()`. Depends on the registry + `args`. No UI.
- `commands/__init__.py` — `register()` precedence + `Completion.source` +
  `command` re-export. No UI.
- Seam wiring (`tui/pane.py`, `web/wssession.py`, `cli.py`) — the only
  UI/CLI-aware code; each delegates to the pure core.

## Testing

Hermetic (`-m "not live"`), TDD — failing test first per unit. Any test that
loads `.aegis/commands/` uses a **temp project dir outside the Workspace**
(`find_project_root` walks up to the Workspace root otherwise).

- **`register()` precedence** — the full matrix: user replaces plugin; plugin
  cannot shadow user (`CommandCollision`); non-builtin cannot shadow builtin
  (2A case preserved); same-source second raises; same-location
  re-registration is idempotent.
- **`expand()`** — `$1..$9` (present + missing→empty), `$ARGUMENTS` (raw
  verbatim, quotes preserved); `@file` include splices content; missing
  `@file` → `ExpandError`; `` !`cmd` `` inlines the fake runner's stdout;
  args-first ordering (`` !`echo $1` `` sees the substituted arg); an argument
  value containing `@x` is *not* re-interpreted as an include.
- **prompt loader** — a temp `.aegis/commands/greet.md` with frontmatter
  registers `/greet` (`summary` from `description`, `usage` from
  `argument-hint`, greedy spec, `source="user"`); invoking its `run` returns a
  `deliver` effect carrying the expanded text; a bad `@file` returns an error
  result (no effect); absent directory → no-op; two files with the same stem →
  first wins + warning.
- **`@command` decorator** — bare form (name/summary/usage defaults);
  kwargs form; registered with `source="plugin"`; a `@command` colliding with a
  builtin raises `CommandCollision`; a non-coroutine or wrong-signature handler
  is rejected at decoration.
- **plugin import** — a temp `plugin_dirs` file with a `@command` registers it
  into `REGISTRY` through `import_plugins()` (integration, hermetic).
- **deliver-effect seam** — TUI `run_test`: submitting a `/`-prompt-command
  mounts a *user* line and calls `core.deliver` (not a command block), and does
  **not** call it for a control command; web unit: a prompt command through the
  `deliver` RPC returns a `{delivery}` frame and calls `core.deliver`, a
  control command still returns `command_result`.
- **palette source-coloring** — `complete()` emits command completions carrying
  the right `source`; a fake registry with one of each source yields three
  distinct `source` values.

Keep test-writing inline (the verification layer). TUI tests are flake-aware
(re-run a failing pane/palette test alone before believing it, per AGENTS.md).

## Security / trust boundary

`.aegis/commands/*.md` is **trusted local project config**, the same trust tier
as `.aegis.yaml`: `@file` reads arbitrary files and `` !`cmd` `` executes
arbitrary shell on every expansion, in the project root. Operator-typed
arguments substitute *before* the include/shell scan (Claude-Code parity), so a
prompt command author can write `` !`git log $ARGUMENTS` `` and argument text is
spliced into the shell/file context that then executes. Because
both the command file (authored by Alex) and the invocation (typed by Alex) are
inside the trust boundary, this is not a privilege-escalation surface — it is
the same capability the `!` shell escape and any `.aegis.yaml`-declared command
already grant. Documented here so the boundary is explicit; no sandboxing in
v1.

## Slices (for the plan)

1. **`register()` precedence + `expand()`** — pure, fully unit-tested. No UI,
   no loader. (`commands/__init__.py`, `commands/expand.py`.)
2. **Prompt loader + boot wiring + deliver-effect seam** — `prompt_loader.py`,
   `cli.py` wiring, and the `deliver` effect case in both `tui/pane.py` and
   `web/wssession.py`. Lands TUI + web together. This is the slice that makes a
   `.aegis/commands/*.md` work end-to-end from both frontends.
3. **Plugin `@command` decorator + import sweep** — `commands/decorator.py`,
   the `aegis.commands.command` export, and the `import_plugins()` integration
   test. Ships an example command under `examples/` (droppable into
   `.aegis/plugins/`).
4. **Palette source-coloring** — `Completion.source`, `complete()` fill, TUI
   tint + web tint. Small additive touch on 2D. TUI + web together.

## Estimate

Comparable to 2B — no genuinely new UI surface (2D built the palette; slice 4 is
a tint). The new code is three small pure modules (`expand`, `prompt_loader`,
`decorator`), the `register()` precedence extension, boot wiring, and one new
`effect` case per seam. Well within a focused span at our pace; each slice is
independently shippable and testable.
