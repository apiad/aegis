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

| tool | glyph | line |
|---|---|---|
| `handoff` | `⇄` | `⇄ weary-turing · "parser is green, render is yours"` |
| `handoff(interrupt=True)` | `⇅` | `⇅ weary-turing · cut · "stop, wrong branch"` |
| `spawn` | `✧` | `✧ calm-hopper · main@vps · "audit the ledger"` |
| `fork` | `✧` | `✧ calm-hopper · forked from weary-turing` |
| `close` | `✦` | `✦ calm-hopper · reaped` |
| `enqueue` | `⇉` | `⇉ general#01K4TZ · "port the fixtures"` |
| queue callback | `⇇` | `⇇ general#01K4TZ · ok · 4m12s` |
| `delegate` | `⇛` | `⇛ general · blocking · "resolve the merge"` |
| `cancel` | `⇎` | `⇎ general#01K4TZ · in-flight` |
| `canvas_write_section` | `▤` | `▤ report §Findings · +18 lines` |
| `canvas_append_to_section` | `▤` | `▤ report §Log · +3 lines` |
| `canvas_open` / `canvas_subscribe` | `▥` | `▥ report · all sections` |
| `group_broadcast` | `⁂` | `⁂ reviewers · 4 members · "review your section"` |
| `group_wait_all` / `group_wait_any` | `⁑` | `⁑ reviewers · waiting on 4` |
| `term_run` | `⌸` | `⌸ build · exit 0 · 12s` |
| `term_keys` | `⌹` | `⌹ build · ^C` |
| `remind` | `◵` | `◵ self · in 20m · "check the tag"` |

**Coordination tier — an act on shared substrate, no addressee.** Same
colour, still bright: other agents are affected even though nobody receives
a message.

| tool | glyph | line |
|---|---|---|
| `claim` | `⊙` | `⊙ exclusive · src/aegis/mcp/ · 3 paths` |
| `release` | `⊚` | `⊚ src/aegis/mcp/` |
| `monitor` | `◷` | `◷ pytest · 62%` |
| `monitor_cancel` | `◶` | `◶ pytest` |
| `loop_stop` | `◼` | `◼ loop · "wired end to end"` |
| `rename` / `title` | `❖` | `❖ aegis-call-format · "design the call format"` |

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

Tool names arrive as `mcp__aegis__aegis_handoff` — the only prefix present
across all 2,616 corpus calls. `descriptor_for()` strips a leading
`mcp__<server>__` and then requires the `aegis_` prefix, so an ACP harness
that does not prefix resolves to the same descriptor.

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
3. **`themes.py`** — one new `aegis` colour role on `AegisColors`. The pale
   tier is that role dimmed, not a second role.

Plus `aegis comms` in `cli.py`.

Nothing in `events.py` changes: `_KIND_BY_NAME` keeps describing native
semantics, and the aegis family is resolved by *name*, not by `kind`.

## Testing

TDD, failing test first, commit per logical unit — repo convention.

- **`descriptors.py` is pure**, so it gets a table of
  `(tool, args) → (glyph, line, target, family)` cases, one row per tool.
  Cheap and exhaustive.
- **A registry-coverage test**: every tool registered by `build_server()`
  has a descriptor. This is the test that stops tool sixty-one from
  silently falling out of the format. Without it the design decays on its
  own.
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
