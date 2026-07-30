---
title: A second Claude driver, on the Agent SDK
date: 2026-07-30
status: design
---

# A second Claude driver, on the Agent SDK

`ClaudeDriver` builds an argv and parses `claude`'s stream-json output by
hand (`drivers/claude.py`, 239 lines; `events.py` for the parse). The
Claude Agent SDK (`claude-agent-sdk` on PyPI, 0.2.128, requires Python
≥3.10 — aegis is 3.13+) wraps the same work as a library.

**It is the same subprocess.** The SDK takes `cli_path` and spawns the
local `claude` binary over the same protocol. This is not a billing
change, not a transport change, and not a migration off the CLI. It is a
swap of *who builds the argv and parses the stream* — us, or Anthropic.

The reason to care is not the parsing. It is that three things aegis has
specced and not built are options on `ClaudeAgentOptions`.

## Prior art

`pingdotgg/t3code` — an open-source control plane over Claude Code,
Codex, OpenCode, Cursor and Grok; structurally the closest public
parallel to aegis, down to shipping its own `effect-acp` package. Its
Claude path is `apps/server/src/provider/Layers/ClaudeAdapter.ts`, 3,951
lines on `@anthropic-ai/claude-agent-sdk@^0.3.170`, and the entire
integration reduces to one `query({...})` call. It is a working
existence proof for every capability claimed below, and the source of
one gotcha worth stealing outright (see *Isolation*).

## What this buys

### `can_use_tool` — the permissions spec, as a callback

```python
async def handler(tool_name: str, input_data: dict,
                  context: ToolPermissionContext) -> PermissionResult:
    ...
options = ClaudeAgentOptions(can_use_tool=handler)
```

Returns `PermissionResultAllow(updated_input=…)` or
`PermissionResultDeny(message=…, interrupt=True)`.

That is `2026-05-27-aegis-fs-tool-surface-design.md`'s `PermissionRouter`
(`allow` / `deny` / `ask` with TUI inline approval), which has a spec and
a plan and no code on disk. It is better than the spec in one respect:
`updated_input` lets the router *rewrite* a tool call, not only gate it.
t3code routes `AskUserQuestion` and `ExitPlanMode` through this same
callback, so the modal-question path is proven.

Note what it does **not** replace: the six `aegis_*` filesystem tools and
`--tools ""` suppression from that spec are a separate half, about
routing filesystem access through the substrate. `can_use_tool` only
supplies the permission half. The spec should be split accordingly, not
declared done.

### `set_model()` / `set_permission_mode()` — TASKS.md 2B.1

`ClaudeSDKClient` exposes both as methods on a live session. TASKS.md
2B.1 (`/model`, `/effort` via resume-restart) is deferred as
"driver-capability-dependent session surgery that warrants its own spec
and TDD pass", and is designed around tearing the session down and
`resume()`-ing with new argv so the conversation survives. On this
driver there is nothing to tear down.

`effort` remains a spawn-time option — there is no `set_effort()`. So
2B.1 splits: `/model` becomes free here, `/effort` still needs the
resume-restart, and only on this driver.

### `session_id` and `fork_session`

`session_id` lets aegis *supply* claude's session id rather than latch
it. `9da13d0` gave every session an immutable log id minted at spawn
(`<timestamp>-<birth handle>`); passing that as `session_id` collapses
two id spaces into one and makes `state/session_log.py` and claude's own
transcript directly correlatable.

`fork_session=True` on resume branches a conversation instead of
continuing it — the conversation-fork affordance named in the vision doc
as Phase 4, for free.

### Smaller, but real

`hooks` (the deferred Tier-B `pre_tool_use` / `post_tool_use` from the
plugin substrate), `max_budget_usd`, `agents` for programmatically
defined subagents, `skills`, `setting_sources` for controlling whether
`CLAUDE.md` and user settings load, `include_partial_messages` as typed
`StreamEvent`s, and `thinking` / `effort` as fields rather than flags.

## What this costs

### The parser is the work

`events.py` is the seam that normalizes Claude **and** ACP into one
canonical event set (`SystemInit`, `AssistantText`, `AssistantThinking`,
`ThinkingTokens`, `ToolUse`, `ToolResult`, `AgentPlan`, `ContextUpdate`,
`Result`, `Unknown`). A new driver must map SDK message types onto that
same set — everything downstream (`render.py`, `state/event_codec.py`,
the web renderer, the whole 7-slice driver-visibility-parity arc)
consumes canonical events and must not notice which driver produced
them.

Two details in the current parser are Claude-specific and easy to drop
on the floor:

- **`parent_tool_use_id`** is set only by the Claude parser, from
  Claude's own stream field. It is the grouping key for `SubagentBox` in
  `tui/pane.py` and its `coalesce.js` mirror in the web client. Lose it
  and subagent transcripts silently render flat.
- **`ParserState.tool_diffs`** synthesizes `ToolResult.diff` from the
  matching `Edit`/`Write` tool_use input, because Claude does not send
  diffs (ACP does, via `FileEditToolCallContent`). The SDK path needs
  the same synthesis.

Both get a test that fails on the SDK driver before it passes.

### `build_argv` is an argv-shaped hole in the seam

`HarnessDriver.build_argv` is `@abc.abstractmethod` and returns
`list[str]`. An SDK-backed driver has no argv — it has an options
object. This is the CLI-driver assumption leaking into the seam.

Two ways out, and the choice is the one real design decision here:

1. **Satisfy it diagnostically.** Return the argv the SDK would produce,
   for logs and `aegis doctor`. Cheap; slightly dishonest.
2. **Demote it.** Make `build_argv` non-abstract on `HarnessDriver` with
   a `return []` default, since it is already only meaningfully
   implemented by CLI drivers.

**Recommend (2).** It is a three-line change, it is true rather than
approximately true, and `AcpDriver.build_argv` already returns
`BASE_CMD` verbatim — the base class is the wrong place for a method
only two of five drivers use for real.

### `pre_spawn` hooks stop applying — a genuine regression

`hooks/runner.run_pre_spawn_hooks` transforms `(argv, env)` before exec
(`drivers/claude.py:79`). With no argv there is nothing for an existing
`pre_spawn` hook to transform. Any plugin using one against a Claude
agent breaks on this driver.

This is the strongest argument for shipping the SDK driver **alongside**
`claude-code` rather than replacing it. The migration story, if we ever
want one, is a Tier-B `pre_options` hook that transforms the
`ClaudeAgentOptions` object — out of scope here, and worth designing
only once something needs it.

### One `append` slot

`system_prompt` takes `{"type": "preset", "preset": "claude_code",
"append": "..."}` — a single string. `build_argv` currently passes
`--append-system-prompt` **twice**: `PRIMING.format(handle=…)` first,
then the persona, deliberately in that order so the agent knows its
handle before the persona lands. Concatenate with the same ordering and
a blank line between. Pin the ordering with a test; it is the sort of
thing that silently reverses in a refactor and only shows up as an agent
that has forgotten its own handle.

### Registration has a known trap

Adding a driver means four places, and the fourth is the one that bit us
before:

1. `DRIVERS` in `drivers/__init__.py`
2. A provider class in `config/__init__.py` (alongside `ClaudeCode`)
3. `IMPLICIT_HARNESSES` in `config/harnesses.py` — derives from
   `_DRIVERS`, so this one is automatic
4. **`_VALID_DRIVERS` at `config/yaml_loader.py:73`**

Missing (4) is exactly the lovelaice bug fixed in `449ebbb`: the
provider model, the driver, and the driver registry all existed, and
`provider: lovelaice` still died with "unknown provider" because the
loader's set never learned the name. A test that declares a
`claude-sdk` agent in YAML and spawns it catches this; a unit test of
the driver class does not.

### Policy, for the record

The Agent SDK docs state: *"Unless previously approved, Anthropic does
not allow third party developers to offer claude.ai login or rate limits
for their products, including agents built on the Claude Agent SDK."*

For aegis as a local tool this is moot — same binary, the operator's own
credentials, nothing offered to anyone. It matters only if aegis is ever
distributed as a product with subscription auth, and it is a reason not
to frame this driver as the path to that.

Separately: the TASKS.md ⚠️ "before June 15 — `claude -p` → REPL mode"
item is dead. Anthropic cancelled that billing split before it took
effect; `-p` and Agent SDK usage both still draw from the subscription
pool. `2026-05-27-aegis-claude-repl-driver-design.md` is superseded by
this document.

## Shape

A **second driver**, registered as `claude-sdk`, beside `claude-code`.
Not a replacement.

`DRIVERS` is already keyed by driver string and `harnesses.py` already
lets one driver register twice under different names, so a second entry
costs nothing structurally. It also means the `pre_spawn` regression and
the parser rewrite are opt-in per agent profile, and a real session can
be A/B'd against the incumbent before anything is promoted.

```
src/aegis/drivers/claude_sdk.py    ClaudeSdkDriver + ClaudeSdkSession
src/aegis/events_sdk.py            SDK message -> canonical Event
```

`ClaudeSdkSession` implements `HarnessSession`: `start` / `send` /
`events` / `close` / `interrupt`, plus `supports_idle_events = True`,
`has_pending_event()`, and the `session_id` property. Claude's
spontaneous between-turn emissions (a Monitor firing) are exactly why
`supports_idle_events` exists, and `ClaudeSDKClient.receive_messages()`
is the natural source for them.

### Isolation

Set **`CLAUDE_CONFIG_DIR`, never `HOME`** when isolating an instance's
config. Overriding `HOME` relocates the macOS keychain lookup, so the
spawned CLI cannot find its stored OAuth credentials and reports "Not
logged in" (t3code, `apps/server/src/provider/Drivers/ClaudeHome.ts`).
Worth carrying into `2026-05-27-agent-sandbox-design.md` regardless of
whether this driver ships.

## Slices

Thinnest first; each is an honest stop point.

1. **Walking skeleton.** `ClaudeSdkDriver` + minimal event mapping
   (`SystemInit`, `AssistantText`, `ToolUse`, `ToolResult`, `Result`),
   registered in all four places, `can_use_tool` allowing everything.
   Done when a live TUI tab holds a real conversation and the transcript
   is indistinguishable from `claude-code`'s.
2. **Parser parity.** The rest of the canonical set, plus
   `parent_tool_use_id` grouping and `tool_diffs` synthesis, each with a
   test that fails before it passes. Done when the same recorded
   conversation renders identically on both drivers.
3. **Resume, session id, fork.** `session_id` fed from the aegis log id;
   `resume`; `fork_session` behind a new `/fork`. Done when a forked tab
   diverges from its parent with both alive.
4. **Permissions.** `can_use_tool` wired to a real router with TUI
   inline approval — the permission half of the fs-tool-surface spec.
   Done when a `deny` is visible in the transcript and the agent
   recovers from it.
5. **Session mutation.** `/model` live via `set_model()`; close out the
   `/model` half of 2B.1.

Slices 1–2 are the actual bet. If parity is not clean, stop there and
keep `claude-code` as the default — nothing else has been disturbed.

## Testing

The live tests need a real `claude` on PATH and are marked `live`, like
`test_integration_live.py`. Use `-m "not live"` for the hermetic suite —
never `-k "not live"`, which matches `live` as a substring and eats
unrelated names.

The parity test in slice 2 is the one that matters: one recorded
conversation, both drivers, assert the canonical event streams match. It
is the only check that can actually fail when a downstream consumer
would.

## Open questions

- Does the SDK surface a `parent_tool_use_id` equivalent on its typed
  messages, or only in the raw partial-message stream? Slice 1 answers
  this by probe, and the answer decides whether slice 2 is small or
  awkward. **Nothing here should be planned past slice 1 without it.**
- Does `interrupt()` reach the same control-request path as the
  hand-rolled `control_request` at `drivers/claude.py:170`?
- `max_budget_usd` overlaps the existing `aegis budget` surface. Which
  owns the ceiling? Deferred — not on the critical path.
