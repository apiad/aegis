---
date_updated: 2026-07-17
scope: aegis slash-command subsystem (src/aegis/commands + TUI/web seams)
generated_by: design-doc skill
---

# Slash commands

Slash commands are commands **aegis itself executes** when typed into the input
box — they never reach the underlying harness (claude / gemini / lovelaice / …).
They sit in the same input-prefix family as the `!` shell escape: a leading
character routes the line to aegis instead of the agent. `/cmd` runs an aegis
command; `//cmd` is an escape that delivers a literal `/cmd` message; anything
else is a normal message.

A command is either a **control command** — a thin, human-facing front-end over
the same `AppBridge` surface agents drive through MCP (spawn a peer, create a
queue, switch themes) that renders its result in the transcript — or a **prompt
command** — a user-authored template that expands and is delivered *to the
agent* as a message. Both are the same `SlashCommand` object in one registry,
distinguished only by what their handler returns
(detail: [Phase 1](superpowers/specs/2026-07-16-aegis-slash-commands-design.md)).

The whole subsystem is harness-agnostic (no Textual/web imports in
`src/aegis/commands/`), so the TUI and the web client are two thin frontends
over one shared core.

## Architecture

```
input line
   │
   ▼
classify_input(text) ──► "//x" → literal message "/x" to the agent
   │                     plain → normal message to the agent
   │ "/x"
   ▼
dispatch(text, ctx) ─── parse(cmd.spec, argstr) ──► Args (or ArgError)
   │                                                   │
   │  REGISTRY[verb].run(ctx, args)  ◄─────────────────┘
   ▼
CommandResult(ok, title, body, effect)
   │
   ├── effect.kind == "deliver"  → seam sends effect["text"] to the agent
   │                               (mount user line + core.deliver)
   └── otherwise                 → seam mounts render_command_block(result)
                                   and applies effect (theme / clear)
```

The core (`src/aegis/commands/`) is pure and frontend-free. Two **seams** wire
it into the frontends and are the only UI-aware code:

- **TUI** — `ConversationPane.on_growing_input_submitted` (`tui/pane.py`) calls
  `dispatch()`, then either mounts a command block or delivers the expansion.
  `_apply_command_effect` applies theme/clear.
- **Web** — `WSSession._deliver_or_command` (`web/wssession.py`) does the same
  at the `deliver` RPC, returning either a `command_result` frame or a normal
  `delivery` frame. Web parity is a first-class part of every slice, not a
  trailing afterthought
  (detail: [2A](superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md)).

The core modules:

| Module | Responsibility |
|---|---|
| `commands/__init__.py` | The `REGISTRY`, the `SlashCommand`/`CommandResult`/`CommandContext` types, `register()` (precedence guard), `dispatch()`, `classify_input()`, and the palette `complete()` + `Completion`/`Completions`. |
| `commands/args.py` | Declarative typed-arg parsing: `Arg`/`Flag`/`ArgSpec`/`Args` + `parse()`. Pure — no registry, no bridge. |
| `commands/expand.py` | Prompt-command template expansion (`$args`, `@file`, `` !`shell` ``). |
| `commands/prompt_loader.py` | Loads `.aegis/commands/*.md` into `source="user"` commands. |
| `commands/decorator.py` | The `@command` plugin primitive → `source="plugin"` commands. |
| `commands/fuzzy.py` | Pure subsequence scorer used by the palette. |
| `commands/builtins/*` | The shipped builtin commands, one module per family. |

## Design decisions

**One registry, three sources, a single `dispatch()`.** Every command — builtin,
user prompt command, plugin command — is a `SlashCommand` in the one module-level
`REGISTRY`, carrying a `source` tag. `dispatch()`, `/help`, and the palette are
all source-agnostic: a new source appears everywhere for free once it registers.
This is why the parser, the collision guard, and the completer seam were built
before any user/plugin loader existed
(detail: [2A](superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md)).

**Control vs. prompt is carried by `CommandResult.effect`, not a command kind.**
Control commands return a `CommandResult` rendered as a transcript block. A
prompt command returns the same type with `effect={"kind": "deliver", "text":
…}`; each seam, already branching on `effect` for theme/clear, gains one case
that routes the text to the agent instead of mounting a block. The distinction
lives entirely in the two seams; `dispatch()` stays pure and never knows the
difference
(detail: [2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md)).

**Declarative typed args, not hand-rolled `.split()`.** Each command declares an
`ArgSpec`; `dispatch()` parses `argstr` into a validated `Args` before calling
the handler, and an `ArgError` becomes a usage error result the handler never
sees. The one shared parser is also what the palette introspects to offer
argument completion
(detail: [2A](superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md)).

**Source precedence resolves collisions deterministically.** `register()` ranks
`builtin > user > plugin`: a higher-priority source replaces a lower one
regardless of load order, and a lower source shadowing a higher one raises
`CommandCollision`. A project-local `.md` command therefore beats an installed
plugin command — "my config beats installed code" — and nothing beats a builtin
(detail: [2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md)).

**Prompt-command expansion mirrors Claude Code.** Arguments substitute first
(so `` !`git log $1` `` works), then `@file` includes and `` !`shell` `` embeds
run over the substituted text. `.aegis/commands/*.md` is trusted local config —
same tier as `.aegis.yaml` — so file reads and shell execution on expansion are
in-boundary, not a privilege surface
(detail: [2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md)).

**Discovery is one pure `complete()` rendered by two drop-ups.** The palette is
an inline drop-up (not a modal), fed by a single harness-agnostic
`complete(text, bridge)` that introspects the registry and each `Arg.completer`.
Ranking is fuzzy-match score with builtins first; there is no history/AI ranking
(detail: [2D](superpowers/specs/2026-07-17-aegis-slash-commands-2d-command-palette-design.md)).

**Collection nouns are plural and a bare noun lists.** `/queues`, `/groups`,
`/schedules`, `/terminals` list on their own and take a subverb (`new`,
`status`, `dissolve`, …) to act
(detail: [2B](superpowers/specs/2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md)).

## Component interfaces

**`dispatch(text, ctx) -> CommandResult`** — strips the leading `/`, splits
`verb` + `argstr`, looks up the verb, parses `argstr` against the command's
`ArgSpec`, and runs the handler. A bare `/` means `/help`; an unknown verb, an
`ArgError`, or any handler exception comes back as an error `CommandResult` — a
bad command never crashes the turn loop. Pure and unit-testable with a fake
bridge.

**`classify_input(text) -> (kind, payload)`** — the precedence ladder shared by
both seams: `//x` → `("message", "/x")` (one slash stripped), `/x` →
`("command", "/x")`, else `("message", text)`. The TUI's `!` shell escape is
handled before this call.

**Command handler** — `async def h(ctx: CommandContext, args: Args) ->
CommandResult`. `ctx.bridge` is the `AppBridge` (the AegisApp / SessionManager);
`ctx.handle` is the calling pane's session handle. Handlers read `args["name"]`;
they never parse strings themselves.

**`CommandResult.effect`** — an optional dict the frontend interprets:
`{"kind": "theme", "name": …}` and `{"kind": "clear"}` mutate the frontend;
`{"kind": "deliver", "text": …}` sends text to the agent. Unknown kinds are
ignored (forward-compatible).

**`complete(text, bridge) -> Completions`** — returns command-name candidates
(verb in progress) or argument-value candidates (past the verb, via the current
`Arg.completer`), plus a ghost `hint`. Never raises: a throwing completer
contributes nothing. Each `Completion` carries a `source` so the frontends tint
by origin.

**Boot loading** — builtins register on package import. At each app boot the
frontend calls `import_plugins(cfg)` (registers `@command`s along with
`@workflow`/`@hook`/`@tool`) and `load_prompt_commands(root)` (registers
`.aegis/commands/*.md`). Boot-load only; editing a `.md` or a plugin takes effect
on the next start (detail:
[2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md)).

### Argument grammar

`parse(spec, argstr)` binds flags and positionals in one left-to-right pass
(detail: [2A](superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md)):

- **Flags lead (and may trail).** `--flag`, `--flag=value`, `--flag value`; a
  `takes_value=False` flag is a boolean presence flag. An unknown `--flag` is an
  error.
- **Non-greedy positionals** take one `shlex`-style token each, so quoting works
  (`/foo "two words"` → one positional).
- **A trailing `greedy` positional** (only the last may be greedy) takes the raw,
  stripped remainder verbatim — quotes and inner `--x` survive — so free-text
  prompts pass through intact.
- Missing required positional, unknown flag, value-less value-flag, unterminated
  quote, or a leftover token with no greedy sink → `ArgError` (its message is
  the human-facing usage error).

## The three sources

**Builtins** (`commands/builtins/`, `source="builtin"`) register at package
import — one module per family, protected from being shadowed. The shipped set
(detail: [2B](superpowers/specs/2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md)):

| Command | Purpose |
|---|---|
| `/help` | List every command (`usage — summary`), grouped by source. |
| `/sessions` | List live agent sessions; mark the active one. |
| `/agents [add … \| remove <slug>]` | List / add / remove agent profiles. |
| `/spawn <agent> [prompt]` | Start a new top-level agent with an optional opening prompt. |
| `/queues [new <name> [agent] [--ephemeral]]` | List / create queues (persisted, or ephemeral in the live manager only). |
| `/enqueue <queue> <payload>` | Drop a task on a queue. |
| `/groups [status \| dissolve <name>]` | List / inspect / dissolve agent groups. |
| `/schedules [show \| enable \| disable \| remove \| logs <name>]` | List / inspect scheduled workflows. |
| `/terminals [new \| run \| close <name> [cmd]]` | List / spawn / run / close shared terminals. |
| `/rename <new>` | Rename the current session. |
| `/close [handle]` | Close the current or a named session. |
| `/themes [name]` | List themes / switch theme (`effect: theme`). |
| `/clear` | Cosmetically clear the transcript (`effect: clear`). |

**User prompt commands** (`commands/prompt_loader.py`, `source="user"`) come
from `.aegis/commands/<name>.md`. Frontmatter `description` → `summary`,
`argument-hint` → `usage`; the body is the template. Each is given a single
greedy positional (`arguments`) so the handler receives the raw rest-of-line and
`expand()` does its own `$1..$9`/`$ARGUMENTS` splitting. The handler returns a
`deliver` effect carrying the expanded text; a bad `@file` include returns an
error result and reaches the agent not at all (detail:
[2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md)).

**Plugin commands** (`commands/decorator.py`, `source="plugin"`) are the fourth
plugin primitive beside `@workflow`/`@hook`/`@tool`, auto-registered on the same
`import_plugins()` sweep. A `@command` handler is an ordinary control command
returning a `CommandResult` (detail:
[2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md);
plugin substrate: [plugins.md](plugins.md)).

## Discovery palette

Typing `/` raises an inline **drop-up** above the input that offers fuzzy-matched
commands, then their subverbs and live argument values (agent / session / queue /
group / schedule / terminal / theme names), with a ghost usage hint. It works
identically in both frontends because both render the output of one
`complete()`; the TUI mounts a `CommandPalette` widget and the web renders a
drop-up `<div>` from a `complete` RPC. Argument values are enumerated through the
`Arg.completer` seam — a static tuple (subverbs) or a callable of the bridge
(dynamic names). Completions are tinted by `source`: builtin = accent, user =
success/green, plugin = working/amber (detail:
[2D](superpowers/specs/2026-07-17-aegis-slash-commands-2d-command-palette-design.md),
source-coloring in
[2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md)).

Keys while the panel is open: Up/Down move the highlight (overriding history
recall), Tab/Enter accept and splice, Esc dismisses; Enter with the panel closed
submits.

## Data model

- **`SlashCommand(name, summary, usage, run, source, spec)`** — the registered
  unit. `name` is unique and lowercased; `source ∈ {builtin, user, plugin}`;
  `spec` is an `ArgSpec`.
- **`CommandResult(ok, title, body, effect)`** — a handler's return. `ok=False`
  renders as an error block; `effect` is the frontend side-effect channel.
- **`CommandContext(bridge, handle)`** — the capability surface + calling handle
  handed to every handler.
- **`ArgSpec(positionals, flags)`** / **`Arg`** / **`Flag`** / **`Args`** — the
  argument grammar and its parsed result.
- **`Completion(insert, label, detail, source)`** / **`Completions(items,
  hint)`** — palette candidates and the ghost hint.
- **`Arg.completer`** — a static `tuple` of choices or a `Callable[[bridge], …]`;
  each choice is a bare value or a `(value, detail)` pair.

## Invariants

- Every command in `REGISTRY` has a unique lowercase `name`.
- A builtin can never be shadowed; between non-builtins, precedence is
  `user > plugin`; equal-source name collisions keep the first and warn.
- Re-registering the *same* command object (a reloaded `.md` file or plugin
  module) is idempotent, not a collision.
- At most one positional is `greedy`, and it is the last positional.
- A prompt command's `ArgSpec` is exactly one optional greedy positional; its
  handler always returns a `deliver` effect on success.
- A `deliver` effect never mounts a command-result block — it is sent to the
  agent as a normal user message in both frontends.
- `dispatch()` and `complete()` never propagate exceptions to the turn loop.
- The `commands/` core imports no Textual or web module.

## Design → spec index

In design order (system → sources → discovery). Cites the authoritative spec per
area; superseded specs are not cited as current.

| Design area | Authoritative spec |
|---|---|
| Concept, registry, `dispatch()`, rendering, `!`/`/`/`//` family | [Phase 1](superpowers/specs/2026-07-16-aegis-slash-commands-design.md) *(registry + dispatch model; its hand-rolled parser is superseded by 2A)* |
| Typed-arg parser, `source`-tagged registry + `CommandCollision`, `//` escaping, `/queues` persistence, web-input parity | [2A](superpowers/specs/2026-07-17-aegis-slash-commands-2a-parser-resolution-design.md) |
| Full builtin coverage, plural-noun convention, `effect` channel (theme/clear) | [2B](superpowers/specs/2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md) |
| Prompt commands (`.aegis/commands/*.md`), `@command` decorator, source precedence, `deliver` effect, palette source-coloring | [2C](superpowers/specs/2026-07-17-aegis-slash-commands-2c-prompt-and-plugin-commands-design.md) |
| Discovery palette: `Arg.completer`, `complete()`, fuzzy scorer, TUI + web drop-ups | [2D](superpowers/specs/2026-07-17-aegis-slash-commands-2d-command-palette-design.md) |

## Out of scope

- **Session-mutation commands** (`/model`, `/effort` via resume-restart) — the
  2B.1 slice, not yet implemented (detail:
  [2B](superpowers/specs/2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md)).
- **Live hot-reload** of `.aegis/commands/*.md` and plugin commands — loading is
  boot-time; edits need a restart.
- **Subverb-dependent argument completion** — a positional's completer is chosen
  by position, not by an earlier positional's value.
- **History / frequency ranking** in the palette — fuzzy-match score only.
- **Deliberately shadowing a builtin** — builtins are immutable to non-builtins.
