# A format for the aegis layer — glyphs on screen, envelopes on disk

**Status:** designed 2026-08-11; not yet planned.
**Scope:** the MCP surface (all `aegis_*` tools plus plugin `@tool`s), the
three renderers (TUI, web, HTML export), and one new state store. No change
to any tool's signature, semantics, or return value.

## The problem

aegis is a multi-agent harness whose entire point is agents coordinating,
and a transcript does not show that happening. A handoff, an enqueue, a
canvas write and a `Bash` call all render as the same grey line with the
same `⏺`.

The cause is mechanical. `describe_tool()` (`src/aegis/render_shared.py:96`)
handles nine native tools by name and falls through for everything else to
*"the first stringy argument, truncated to 60 characters"*. Every `aegis_*`
tool takes that fallback. The icon comes from `KIND_ICON[ev.kind]`, and
`_KIND_BY_NAME` (`src/aegis/events.py:271`) knows only native tools, so
every aegis call resolves to `"other"` → `⏺`.

So `aegis_handoff(from_handle="…", target_handle="weary-turing",
context="the parser is green, the render is yours")` renders as the first 60
characters of whichever argument happens to be first in the dict. The one
fact worth showing — *this agent just spoke to weary-turing* — is the one
fact the line does not carry.

The corpus says how much this costs. Across `.aegis/state/sessions/` there
are **2,616** aegis calls, all named `mcp__aegis__aegis_*`:

| tool | calls | | tool | calls |
|---|---:|---|---|---:|
| `monitor` | 726 | | `spawn` | 62 |
| `handoff` | 605 | | `term_run` | 60 |
| `claim` | 212 | | `monitors` | 50 |
| `rename` | 208 | | `monitor_cancel` | 50 |
| `release` | 181 | | `enqueue` | 45 |
| `meta` | 122 | | `claims` | 39 |
| `list_sessions` | 97 | | `canvas_append_to_section` | 24 |

There is a second, quieter half. The *inbound* direction is already typed —
`render_inbox_header` stamps `> from agent:<handle> · <ts>` and
`> from queue:<name> · task#<id> · ok` on every delivered message — but the
*outbound* direction is not recorded anywhere. After the fact there is no
way to reconstruct who handed what to whom, in what order, or which callback
answered which enqueue. The inbox knows what arrived; nothing knows what was
sent.

## The shape

Two artifacts from one registry.

**On screen** — one glyph, one bright counterpart, dim details:

```
⇄ weary-turing · "parser is green, render is yours"
✧ calm-hopper · main@vps · "audit the ledger"
⇉ general#01K4TZ · "port the fixtures"
⊙ exclusive · src/aegis/mcp/ · 3 paths
▤ report §Findings · +18 lines
◷ pytest · 62%
∘ 7 sessions
```

**On disk** — one envelope per call, in an append-only ledger:

```json
{"v": 1, "call_id": "01K4TZ…", "ts": "2026-08-11T14:22:07Z",
 "from": "aegis-call-format", "to": {"kind": "agent", "id": "weary-turing"},
 "family": "conversation", "verb": "handoff", "thread": "01K4TZ…",
 "outcome": "ok", "duration_ms": 41}
```

Both read from `src/aegis/comms/descriptors.py`. One place knows what
matters about each tool; the renderer and the ledger are two consumers of
that one answer. If they diverged on who the counterpart is, the ledger
would be lying about a conversation the screen showed correctly.

## The line grammar

```
<glyph> <counterpart> · <detail> · <detail>
```

- **glyph** — exactly one, leading, in the `aegis` colour role. Never a
  second semantic glyph in the body, and never an arrow: direction is
  encoded in the glyph itself (`⇄` out to a peer, `⇇` back from a queue).
- **counterpart** — who or what is on the other end, and the only bright
  segment: a handle, a `queue#task`, a `canvas §section`, a group, a path.
  This is the segment that says *agents talking*.
- **details** — dim, `·`-separated, capped in width.

### Three groups, two visual weights

Native tools use emoji (`📖 ✏️ 🔎 🌐`). The aegis family is geometric and
monochrome in a single colour role, so the layer is legible at a glance
without spending an extra cell on a sigil. Within the family:

**Bright tier — a named counterpart exists.** This is the conversation.

A glyph names a **semantic act**, not a tool, so tools that mean the same
thing share one. Nineteen acts cover all 35 bright tools; a per-tool glyph
for each would be nineteen distinctions the reader has to learn and thirty-five
rare codepoints to hope the terminal font carries.

| glyph | act | tools |
|---|---|---|
| `⇄` | hand context to a peer | `handoff` |
| `⇅` | cut a peer's turn | `handoff(interrupt=True)` |
| `✧` | bring an agent into being | `spawn`, `fork`, `group_spawn`, `group_spawn_mixed` |
| `✦` | reap an agent | `close` |
| `⇉` | send work to a queue | `enqueue`, `delegate` |
| `⇎` | kill queued work | `cancel` |
| `⁂` | speak to a group | `group_broadcast` |
| `⁑` | wait on a group | `group_wait_all`, `group_wait_any` |
| `⌗` | reshape a group | `group_rename`, `group_dissolve`, `group_move_member` |
| `▤` | write a canvas section | `canvas_write_section`, `canvas_append_to_section` |
| `▥` | attach to a shared surface | `canvas_open`, `canvas_subscribe`, `term_spawn`, `term_subscribe` |
| `▧` | detach from one | `canvas_unsubscribe`, `term_unsubscribe`, `term_close` |
| `■` | drive a terminal | `term_run`, `term_keys` |
| `⊙` | take a claim | `claim` |
| `⊚` | drop a claim | `release` |
| `◷` | arm a waker | `monitor`, `remind` |
| `◶` | disarm a waker | `monitor_cancel`, `reminder_cancel` |
| `◼` | stop a loop | `loop_stop` |
| `❖` | rename or retitle self | `rename`, `title` |

```
⇄ weary-turing · "parser is green, render is yours"
⇅ weary-turing · cut · "stop, wrong branch"
✧ main@vps · "audit the ledger"
✦ calm-hopper · reaped
⇉ general · "port the fixtures"
⁂ reviewers · "review your section"
▤ report §Findings · +18 lines
■ build · pytest -q
⊙ exclusive · src/aegis/mcp/ · 3 paths
◷ pytest · 62%
❖ aegis-call-format · "design the call format"
```

`spawn` shows `<profile>@<host>`, not a handle: the handle does not exist
until the call returns, and the transcript line is built from the call's
arguments.

The queue *callback* (`⇇ general#01K4TZ · ok`) is deliberately absent. It is
not a tool call — it arrives as an inbox message and `render_inbox_header`
already gives it a typed format. Touching that is a separate change.

**Pale tier — reading the room.** `list_sessions`, `list_agents`, `claims`,
`monitors`, `reminders`, `canvas_list`, `term_list`, `peer_plan`,
`read_peer`, `view_file`, `meta`, `task_status`, `workflow_status`, and
every `config_*` / `schedule_*` / `run_workflow` admin call. One shared
glyph `∘`, rendered *dimmer than a native tool line*.

```
∘ 7 sessions
∘ claims · 3 held
∘ config · 12 agents
```

The grouping is the design, not decoration. `monitor` alone is 28% of all
aegis calls and `meta`/`list_sessions`/`claims`/`monitors` together are 308.
Give every aegis call the same visual weight and the signature is eaten by
polling — the current failure with better typography.

### Width

The geometric glyphs are East Asian Ambiguous: Rich measures one cell, many
terminals draw wider. They are always followed by a space, and every width
budget is computed with `cell_len`, never `len()` — the same trap already
paid for by the plan-strip circles and documented in `AGENTS.md`.

## The envelope

One `Envelope` per call, minted by a FastMCP middleware. Fields:

| field | source |
|---|---|
| `v` | schema version, `1` |
| `call_id` | `new_ulid()` — `queue/schema.py:66` |
| `ts` | `now_iso()` — `queue/schema.py:82` |
| `from` | `args["from_handle"]`, best-effort (see below) |
| `to` | `descriptor.target(args)` → `{kind, id}` or `null` |
| `family` | `conversation` \| `coordination` \| `introspection` \| `admin` |
| `verb` | tool name minus the `aegis_` prefix |
| `thread` | the substrate id when one exists, else `call_id` |
| `outcome` | `ok` \| `error` |
| `duration_ms` | measured across the call |

The substrate ids live in the *result*, not the arguments — `enqueue`
returns `{"task_id": …}`, `monitor` returns `{"monitor_id": …}` — and the
middleware sees both sides: `result.structured_content` carries the tool's
dict verbatim. A failing tool raises `fastmcp.exceptions.ToolError` through
the middleware, which is how `outcome: error` is detected.

**`to` is typed, not a string.** `kind` is one of `agent`, `queue`,
`canvas`, `group`, `term`, `path`, `self`, or absent. The renderer's bright
counterpart and the ledger's `to` come from the same `descriptor.target()`.

**`thread` correlates the round trip.** The substrate already mints ids for
nearly everything it does: `task_id`, `monitor_id`, `reminder_id`,
`workflow_run_id`, `claim_id`. The envelope adopts them, so an `⇉ enqueue`
and the `⇇ callback` that lands in another agent's inbox four minutes later
share a thread, and `⊙ claim` / `⊚ release` close theirs. `handoff` is the
exception — it is fire-and-forget and has no substrate id — so it threads on
its own `call_id`.

### `from` is best-effort, and says so

The MCP server is co-resident and shared: every agent on this aegis reaches
the same port, so there is no per-connection identity to read a handle from.
That is why `from_handle` is a *parameter* in the first place, and why
`list_sessions`, `claims` and `canvas_list` do not ask for one.

The envelope does not invent it. A call with no `from_handle` is recorded
unattributed and rendered unattributed. This is deliberate: nothing enforces
the convention today, so the gap already exists — the ledger only makes it
visible. A per-session MCP token is the real fix, and a follow-up rather
than part of this slice.

### The ledger

`.aegis/state/comms/YYYY-MM-DD.jsonl`, one per aegis instance rather than
per handle — the whole value is the cross-agent view.

Writes reuse `queue/jsonl.py::append_record`, which already creates parent
dirs and stamps `v: SCHEMA_VERSION`. Reads do **not** reuse `read_records`:
it calls `json.loads` per line and raises on a truncated trailing record,
which is the correct behaviour for queue replay (a corrupt lifecycle log
should stop the boot) and the wrong one here. So `comms/persistence.py`
carries its own tolerant reader, skipping damaged lines the way
`state/session_log.py::scan_log` does. A damaged ledger degrades to the
records it can parse and never raises into a paint or a tool call.
Changing `read_records` itself is out of scope — it would flip queue and
inbox replay semantics for an unrelated reason.

Failure in the middleware is never allowed to fail the tool. A write error
is logged and swallowed; the agent's call returns normally. The ledger is
observability, and observability that can break the thing it observes is a
liability.

### And a reader, in the same slice

```
aegis comms [--handle X] [--thread T] [--since 1h] [--family conversation]
```

Prints the reconstructed conversation. Not for completeness: a write-only
ledger is *unverifiable* — there is no way to know it works except reading
the file by hand, which is precisely the proxy-signal trap. The reader is
how the artifact gets exercised the way it will actually be used.

## Components

New package `src/aegis/comms/`, shaped like `locks/` and `groups/`:

- **`descriptors.py`** — `AegisToolDescriptor(verb, family, glyph,
  describe, target)`, the registry keyed by bare verb, and
  `descriptor_for(name)`. Pure: no Rich, no I/O, no bridge. The single
  place that knows what matters about each tool.
- **`models.py`** — `Envelope`, `Target`.
- **`persistence.py`** — JSONL writer + `read_envelopes()`.
- **`middleware.py`** — a `fastmcp.server.middleware.Middleware` subclass
  implementing `on_call_tool`, mounted in `build_server()`
  (`src/aegis/mcp/server.py:588`).

`on_call_tool` is the choke point every call passes through, including
plugin `@tool`s. Wrapping tools individually would work for the sixty that
exist and silently miss the sixty-first.

### Name normalisation

The two consumers see two different names, verified against a live FastMCP
3.2.0 server:

- The **renderer** sees what the harness called the tool. In the transcript
  corpus that is `mcp__aegis__aegis_handoff`, on all 2,616 calls.
- The **middleware** sees the bare registered name, `aegis_handoff`.
  `context.message.name` never carries the `mcp__` prefix.

So `descriptor_for()` strips a leading `mcp__<server>__` and then requires
the `aegis_` prefix. The middleware passes through the same function
unchanged, and an ACP harness that does not prefix resolves identically.

### Three sutures

1. **`describe_tool()`** gains an early branch delegating to
   `descriptor_for(name)`. Because `web/compact.py:35` already precomputes
   `desc` server-side and strips `raw_input` from the wire, the web client
   inherits every line for free.
2. **The glyph** — `render.py:115` and `render_html.py:42` become
   `aegis_glyph(ev.name) or KIND_ICON.get(ev.kind or "", "⏺")`, and
   `compact.py` sends the resolved glyph on the wire beside `desc`.
   `renderEvent.js:189` prefers `ev.icon` when present **and its private
   copy of `KIND_ICON` (`renderEvent.js:7`) is deleted.** That duplicated
   table is existing debt; adding a second parallel table to it is not an
   option, so this slice pays it.
3. **`themes/__init__.py`** — one new `comms` field on `AegisColors`,
   derived from `theme.primary`. No theme YAML is edited: `primary` is
   required by `to_textual_theme()` so all three bundled themes already
   carry it, and `aegis_colors()` is currently the one consumer that never
   reads it. `to_css_variables()` already emits `--aegis-primary`, so the
   web needs no new variable either. The pale tier is that colour dimmed,
   not a second role.

Plus `aegis comms` in `cli.py`.

Nothing in `events.py` changes: `_KIND_BY_NAME` keeps describing native
semantics, and the aegis family is resolved by *name*, not by `kind`.

## Testing

TDD, failing test first, commit per logical unit — repo convention.

- **`descriptors.py` is pure**, so it gets a table of
  `(tool, args) → (glyph, line, target, family)` cases, one row per tool.
  Cheap and exhaustive.
- **A registry-coverage test**: every tool `await server.list_tools()`
  reports has a descriptor. Today that is **72** tools — 35 bright, 37
  pale. This is the test that stops tool seventy-three from silently
  falling out of the format. Without it the design decays on its own.
- **Middleware**: a call that succeeds and a call that raises produce two
  envelopes with the right `outcome` and a present `duration_ms`; a
  middleware that raises internally does **not** fail the tool.
- **Ledger round-trip**, including a file whose last line is truncated
  mid-record.
- **Mutation of the gate**: break `descriptor_for` deliberately and confirm
  the coverage test goes red. A gate that cannot fail is worth less than no
  gate, because it licenses shipping.
- **Width**: assert on `cell_len` of rendered lines at the narrow tier, not
  `len()`.

## Out of scope

- **Per-session MCP identity.** The real fix for `from`. Follow-up.
- **A COMMS section in the `F3` sidebar.** The ledger makes it possible;
  this slice ships the CLI reader, which is enough to verify the artifact.
- **Emitting envelopes as live events.** The render tier already covers the
  live view; a second live plane would be a parallel truth.
- **Re-rendering historical transcripts.** New lines get the format; old
  logs render as they always did.
