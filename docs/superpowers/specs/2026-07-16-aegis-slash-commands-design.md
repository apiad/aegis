# Slash commands — design spec

**Date:** 2026-07-16
**Status:** Quick win approved — implementing Phase 1 inline
**Owner:** Alex + Claude

## Summary

Slash commands are commands **aegis itself executes** when typed into the
input box — they never reach the underlying harness (claude / gemini /
lovelaice / …). They sit in the same input-prefix family as the `!` shell
escape: a leading character routes the line to aegis instead of the agent.

- `!cmd` → run a local shell command, inject output as a message (shipped).
- `/cmd` → run an aegis **control command** (this spec).
- anything else → a normal message to the agent.

A control command is a thin, human-facing front-end over the capabilities
aegis already exposes to agents through the MCP `AppBridge`
(`spawn`, `list_sessions`, `register_queue`, `handoff`, …). Slash commands
are that same surface, driven from the keyboard: **a second front-end over
`AppBridge`, parallel to the MCP plane.**

## Motivation

Today, to make aegis *do* something (spawn a peer, create a queue, drop a
task on it) you either click through the TUI or ask an agent to call an MCP
tool on your behalf — spending a turn and tokens to do something aegis can
do directly. Slash commands let the operator drive the meta-harness
straight from the input box, no agent round-trip.

## Non-goals (Phase 1 / quick win)

Everything below is deferred to the larger spec (see *Later*):

- **Prompt commands** (user-authored `.aegis/commands/*.md` that expand to a
  message sent to the agent). Phase 1 is control commands only.
- **Plugin-contributed commands** (`@command` decorator).
- **Autocomplete / command palette / fuzzy discovery.**
- **Typed arguments, quoting, flags.** Phase 1 parses `first token =
  subcommand, rest-of-line = payload`.
- **Web-client parity.** Phase 1 is TUI-only, exactly like `!`. The
  dispatcher is written harness-agnostic so the web client can reuse it
  later, but no WS wiring in Phase 1.
- **Persisting runtime-created queues to `.aegis.yaml`.** Phase 1 registers
  queues in the live `QueueManager` only (gone on restart).

## Design (Phase 1)

### Package layout

A new harness-agnostic package `src/aegis/commands/`:

- `__init__.py` — the registry, the `SlashCommand` / `CommandResult` /
  `CommandContext` types, and the pure `dispatch()` entry point. No Textual
  import, so the web client can reuse it verbatim later.
- `builtins.py` — the Phase 1 commands, registered on import.

### Types

```python
@dataclass(frozen=True)
class CommandResult:
    ok: bool          # False → rendered as an error block
    title: str        # e.g. "spawned researcher-1"
    body: str = ""    # multi-line detail (may be empty)

class CommandContext:
    bridge: AppBridge        # = the AegisApp; the shared capability surface
    handle: str              # the current pane's session handle (for spawned_by)

@dataclass(frozen=True)
class SlashCommand:
    name: str
    summary: str                       # one line, shown by /help
    usage: str                         # e.g. "/spawn <agent> [prompt]"
    run: Callable[[CommandContext, str], Awaitable[CommandResult]]
```

### Registry & dispatch

- Module-level `REGISTRY: dict[str, SlashCommand]`; `register(cmd)` adds,
  builtins call it on import.
- `async def dispatch(text: str, ctx: CommandContext) -> CommandResult`:
  1. Strip the leading `/`, split into `verb` and `argstr` (`rest-of-line`).
  2. Empty verb (`/` alone) → treat as `help`.
  3. Unknown verb → `CommandResult(ok=False, title="unknown command: /verb",
     body="try /help")`.
  4. Otherwise `await cmd.run(ctx, argstr)`. Any exception from a handler is
     caught and returned as an error result (a bad command never crashes the
     turn loop).

`dispatch` is pure (no UI), unit-testable with a fake bridge.

### Dispatch seam

In `ConversationPane.on_growing_input_submitted`, after the existing `!`
branch and before message delivery:

```python
if text.startswith("/"):
    ctx = CommandContext(bridge=self.app, handle=<this pane's handle>)
    result = await dispatch(text, ctx)
    self._mount_block(render_command_block(result, self._palette, width),
                      f"{result.title}\n{result.body}")
    return   # never delivered to the agent
```

Precedence: `!` shell > `/` command > plain message. A message that must
start with a literal `/` is a known limitation in Phase 1 (escaping via `//`
is in the larger spec).

### The four commands

| Command | Calls | Result |
|---|---|---|
| `/help` | registry read | Lists every command: `usage — summary`. |
| `/sessions` | `bridge.list_sessions()` | Table of `handle · agent · state` (marks the active one). |
| `/agents` | `bridge.list_agents()` (+ `_agents` detail) | Configured agent profiles: `name · harness · model · permission` (bare names if detail unavailable). |
| `/spawn <agent> [prompt]` | `bridge.spawn(agent, opening_prompt=prompt or None, spawned_by=ctx.handle)` | `spawned <handle>`; error if `<agent>` not in `bridge.list_agents()`. |
| `/queue new <name> [agent]` | build `Queue(name, agent_profile=agent-or-default, max_parallel=1)`, `bridge.register_queue(q)` | `queue <name> created`; error (collision / unknown agent) surfaces `ValueError`. |
| `/enqueue <queue> <payload>` | `bridge.queue_manager.enqueue(queue, payload, enqueued_by=sender_user(), callback=False)` | `queued task <id> at position <n>`; error on unknown queue. |

`/queue` is one command with subverbs (`new`); `/enqueue` is its own verb
for ergonomics. Default agent for `/queue new` with no agent arg =
`bridge.list_agents()[0]`.

### Rendering

`render_command_block(result, palette, width) -> RenderableType` lives in
`render.py` beside `render_inbox_block` / `render_user_line`. A distinct
`/`-glyph header in the accent colour; `body` beneath; the whole block
tinted `$error` when `result.ok is False`. Mounted in the transcript like
any other block — scrollable, leaves a trace.

### Input accent

Mirroring the `!` shell-escape magenta outline: while the input starts with
`/`, the pane carries a `slash-command` class that turns the input outline
**and the typed text** bright blue, so a command reads as visually distinct
from a message (green idle) or a shell escape (magenta text + outline).
Precedence of outline states: recording > shell-escape > slash-command >
working > idle. Toggled in the same `on_text_area_changed` handler.

## Testing (Phase 1)

- `dispatch()` unit tests with a fake `AppBridge`: `/help` lists commands;
  unknown → error result; `/spawn missing` → error; `/spawn <ok>` calls
  `bridge.spawn` with the right args; `/queue new` builds+registers a Queue;
  `/enqueue` calls `queue_manager.enqueue`; handler exception → error result.
- Pane test (Textual `run_test`): typing `/sessions` mounts a command block
  and does **not** call `session.send`; typing `/` toggles the
  `slash-command` class (bright-blue outline), cleared on submit.

## Later (larger spec — tomorrow)

A separate spec will cover the *powerful* slash-command system built on the
Phase 1 registry + dispatcher + rendering (no rework):

- **Prompt commands** — user-authored `.aegis/commands/<name>.md`
  (frontmatter + `$1` / `$ARGUMENTS` template, `@file` includes, embedded
  `!shell`), expand → sent as a message to the agent (Claude-Code parity).
- **Full builtin coverage** — every `AppBridge` / MCP capability as a
  command: `/group`, `/schedule`, `/handoff`, `/rename`, `/model`,
  `/effort`, `/theme`, `/clear`, `/close`, terminals, config.
- **Plugin-contributed commands** — a `@command` decorator beside the
  existing `@workflow` / `@hook` / `@tool` primitives.
- **Discovery UX** — type `/` → autocomplete dropdown / palette, fuzzy
  match, tab-completion of agent / queue / session names.
- **Typed arguments** — usage specs, quoting, flags.
- **Web parity** — web input dispatches through the same registry via a new
  WS `slash` message; same block type in the web transcript.
- **Escaping & namespacing** — `//` for a literal leading slash; builtin vs
  user vs plugin resolution order; queue persistence to `.aegis.yaml`.
