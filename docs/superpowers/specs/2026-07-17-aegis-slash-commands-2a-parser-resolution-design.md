# Slash commands 2A — Parser + Resolution Core — design spec

**Date:** 2026-07-17
**Status:** Approved — ready for implementation plan
**Owner:** Alex + Claude
**Builds on:** `2026-07-16-aegis-slash-commands-design.md` (Phase 1, shipped v0.17.0)

## Summary

Phase 1 shipped a deliberately dumb slash-command dispatcher: `/verb
rest-of-line`, one flat `REGISTRY`, and each handler parsing its own
`argstr` by hand (`src/aegis/commands/__init__.py`, `builtins.py`). Phase 2
is the *powerful* system, and it is too large for one spec — it spans
parsing infrastructure, whole new features (prompt commands, discovery UX),
and a cross-cutting web-parity concern. It is therefore decomposed into
sub-specs, each with its own spec → plan → implement cycle:

- **2A (this spec)** — parser + resolution core: typed args, protected-builtin
  resolution model with source tags, `//` escaping, `/queue new` persistence,
  and web-input parity for the slash surface.
- **2B** — full builtin coverage (`/group /schedule /handoff /rename /model
  /effort /theme /clear /close`, terminals, config). Depends on 2A's parser.
- **2C** — prompt commands (user `.aegis/commands/*.md`) + plugin `@command`
  decorator. Both plug into 2A's resolution order.
- **2D** — discovery UX (`/` autocomplete / palette / fuzzy match), which
  introspects 2A's `ArgSpec`.

Web parity is threaded *through each* sub-spec (every slice lands in TUI +
web together), not deferred to a trailing sub-project.

2A builds the foundation everything else needs. It adds **no new command
sources** — the user-`.md` and plugin loaders are 2C — but it builds the
seams those loaders plug into (the `source`-tagged registry, the collision
guard) and the typed-arg surface 2D introspects.

## Motivation

Phase 1's "each handler parses `argstr` by hand" is fine for six commands
but does not scale: 2B adds ~11 more, 2C lets users and plugins contribute
commands (which can collide with builtins), and 2D needs to introspect
argument names to offer autocomplete. Hand-rolled `.split()` in every
handler gives none of that. 2A replaces that freedom with one shared,
declarative, typed parsing layer, decides the collision policy once, and
brings the web input box to parity with the TUI so the slash surface works
identically in both frontends.

## Non-goals (2A)

- **New command sources.** User-`.md` prompt commands and the plugin
  `@command` decorator are 2C. 2A builds the registry seam (`source` tag +
  collision guard) they use, and unit-tests it with a synthetic non-builtin
  command, but ships no loader.
- **Discovery UX.** No autocomplete / palette. 2A only guarantees the
  `ArgSpec` is introspectable so 2D can build on it.
- **Full argparse semantics.** No sub-parsers, no nargs, no choices, no type
  coercion beyond string/bool. Three positional shapes + two flag shapes,
  deliberately.
- **Overriding builtins.** Builtins are immutable in 2A (protected). A
  "deliberately shadow a builtin" mode is deferred until there is real
  demand.

## Design

### 1. Arg spec + parser — new module `src/aegis/commands/args.py`

```python
@dataclass(frozen=True)
class Arg:
    name: str
    required: bool = True
    greedy: bool = False          # consumes the raw rest-of-line; last positional only

@dataclass(frozen=True)
class Flag:
    name: str                     # "effort" matches --effort
    takes_value: bool = True      # False → boolean presence flag
    default: str | bool | None = None

@dataclass(frozen=True)
class ArgSpec:
    positionals: tuple[Arg, ...] = ()
    flags: tuple[Flag, ...] = ()

@dataclass(frozen=True)
class Args:
    positional: dict[str, str]    # name → value
    flags: dict[str, str | bool]  # name → value (default-filled)
    def __getitem__(self, k): ...  # positional first, then flags
    def get(self, k, default=None): ...

class ArgError(ValueError): ...    # message is human-facing
```

`parse(spec: ArgSpec, argstr: str) -> Args`.

**Parsing rule — flags lead.** Walk tokens left-to-right consuming
`--flag` / `--flag=value` / `--flag value` while the token names a declared
flag; a declared boolean flag (`takes_value=False`) consumes no value.
After the leading flag run, bind positionals in order:

- Each non-greedy positional takes one `shlex`-tokenized token (so quoting
  works: `/foo "two words"` → one positional).
- A trailing `greedy` positional (only the last positional may be greedy)
  takes the **raw, stripped remainder** of the original string from that
  point — *not* `shlex`-split — so free-text survives verbatim, quotes and
  all: `/spawn researcher write a poem "keep the quotes"` gives
  `positional["prompt"] == 'write a poem "keep the quotes"'`.

`parse` raises `ArgError` on: a required positional left unbound, an unknown
`--flag`, a value-taking flag with no value, or a leftover token when the
last positional is not greedy. Flags not supplied are filled from their
`default` (boolean flags default `False` when no default given).

**Boundary decisions (made explicit to avoid ambiguity):**
- Flags appearing *after* positionals begin are not parsed as flags — once
  positional binding starts, `--x` is just a token / part of the greedy
  remainder. Flags lead, always.
- An unknown `--flag` is an error even if it appears in the flag-run region;
  we do not silently pass it through.
- `shlex.split(..., posix=True)` handles the non-greedy tokenization.

### 2. Registry with sources — edit `src/aegis/commands/__init__.py`

- `SlashCommand` gains two fields: `source: str = "builtin"` and
  `spec: ArgSpec = ArgSpec()`.
- **Handler signature changes** from `Callable[[CommandContext, str], ...]`
  to `Callable[[CommandContext, Args], ...]`. `dispatch()` parses `argstr`
  with `cmd.spec` *before* calling the handler; on `ArgError` it returns
  `CommandResult(ok=False, title="usage: <cmd.usage>", body=str(err))` and
  never calls the handler. Handlers receive a validated `Args`.
- The six Phase-1 builtins in `builtins.py` migrate: each declares an
  `ArgSpec` and reads `args["name"]` instead of splitting `argstr`. This is
  the bulk of the mechanical work and is contained to `builtins.py`.
- `register(cmd)` enforces the **protected-builtin** rule: if `cmd.source`
  is not `"builtin"` and a command of the same `name` already exists with
  `source == "builtin"`, `register` raises `CommandCollision(ValueError)`.
  Builtins registering themselves at import are unaffected (they *are* the
  protected set). 2C's loaders call `register` inside try/except and surface
  the collision as a load-time warning; the builtin wins.
- `/help` groups its listing by `source` (builtins first) and prints
  `usage — summary` per command.

### 3. `//` escaping — at the dispatch seams

The literal-slash escape lives at the two call sites, not inside
`dispatch()` (which only ever sees command text). Both
`tui/pane.py:on_growing_input_submitted` and `web/wssession.py` `deliver`
apply the same precedence ladder:

1. `text.startswith("//")` → strip exactly one leading `/`, deliver the
   remainder to the agent as a normal message (a literal leading slash is
   preserved).
2. `text.startswith("!")` → shell escape (TUI only; unchanged).
3. `text.startswith("/")` → slash command via `dispatch()`.
4. otherwise → normal message.

### 4. `/queue new` persistence — edit `builtins.py`

`/queue new <name> [agent] [--ephemeral]`.

- Default (no `--ephemeral`): call
  `aegis.config.edit.add_queue(root, name, agent=agent or default_agent,
  max_parallel=1)` — the same comment-preserving helper the
  `aegis config add-queue` CLI and `aegis_config_add_queue` MCP tool use —
  to persist to `.aegis.yaml`, **and** hot-register in the live
  `QueueManager`. `add_queue` fails loud on duplicate name / unknown agent;
  that surfaces as an error `CommandResult`.
- `--ephemeral` (boolean flag): live `QueueManager` registration only, no
  write — the Phase-1 behavior, kept as an opt-in.
- Default agent when `agent` omitted = `bridge.list_agents()[0]`, as in
  Phase 1. `root` comes from the bridge / project root the session was
  started under.

### 5. Web parity — edit `web/wssession.py` + a web command block

Today the `deliver` RPC (`wssession.py:252`) sends the raw message straight
to `core.deliver` with no `/` or `!` handling. 2A adds:

- The precedence ladder from §3 (minus the TUI-only `!`) at the top of the
  `deliver` path: `//` unescapes to a literal message; `/` routes through
  the same `dispatch()` (the commands core is already Textual-free, so the
  web reuses it verbatim); anything else delivers as before.
- A new response frame for the command case:
  `{command_result: {ok, title, body}}` instead of the usual
  `{delivery, depth}`.
- The web client renders `command_result` as a command block mirroring the
  TUI's `render_command_block` (distinct `/`-glyph header, error tint on
  `ok=False`), mounted in the transcript like any other block.

This is the slice's honest stop point: after 2A, `/sessions`, `/spawn`,
`/queue new`, etc. work identically from the web input box and the TUI
input box.

## Component boundaries

- `args.py` — pure parsing. No registry, no bridge, no UI. Testable with
  `ArgSpec` + string in, `Args` or `ArgError` out.
- `__init__.py` — registry + `dispatch()`. Depends on `args.py`. No UI.
- `builtins.py` — the concrete commands. Depends on registry + `args.py` +
  the bridge protocol + `config.edit`.
- Seam wiring (`tui/pane.py`, `web/wssession.py`) — the only Textual /
  web-aware code; both delegate to the pure core.

## Testing

Hermetic (`-m "not live"`), TDD — failing test first per unit:

- **`parse()`** — required / optional / greedy positionals bind correctly;
  flags-lead parsing (`--flag`, `--flag=v`, `--flag v`, boolean flag);
  quoting survives on non-greedy tokens; greedy remainder is raw/verbatim;
  each `ArgError` path (missing required, unknown flag, flag missing value,
  excess token without greedy).
- **registry** — a `builtin` registers; a `user`-source command colliding
  with a builtin raises `CommandCollision`; a `user`-source command with a
  fresh name registers; `/help` groups by source.
- **`dispatch()`** — `ArgError` returns an error result whose title is the
  usage string and never calls the handler; a valid line hands the handler a
  populated `Args`; unknown verb still errors as in Phase 1.
- **`//` escape** — TUI `run_test`: `//foo` delivers `/foo` as a message,
  does not dispatch; web unit: same at the `deliver` seam.
- **`/queue new`** — with a fake `config.edit` root, default persists (write
  observed) and hot-registers; `--ephemeral` skips the write but still
  hot-registers; duplicate name → error result.
- **web** — `/sessions` through the `deliver` RPC returns a `command_result`
  frame and does **not** call `core.deliver`; a plain message still calls
  `core.deliver`.

## Estimate

One implementable slice, well under a day at our pace. The mechanical bulk
is migrating the six Phase-1 builtins to `ArgSpec` + `Args`; the genuinely
new code is `args.py` (small, pure) and the web `deliver` seam.
