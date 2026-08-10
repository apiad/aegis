# `/spawn` carries where you were standing

**Status:** implemented (2026-08-10)
**Spec:** `docs/superpowers/specs/2026-08-10-aegis-spawn-with-provenance-design.md`

## The gap

`/spawn opus please verify this test` starts a new agent whose entire
world is the string `please verify this test`. Which test? The operator
knows — they are looking at it, three turns up their own tab. The new
agent is not, and has no way to find out: it does not know it was
spawned from anywhere, let alone from where.

So the operator either retypes the context (the thing `@peer` was built
to stop) or watches a fresh agent burn its first four turns grepping for
a referent that was one log read away.

`@peer` already solved exactly this for a *live* target:

| | context carried | cost | target |
|---|---|---|---|
| `aegis_handoff` | none — you retype it | free | live peer |
| `@peer` | a bounded slice of where you are | one turn | **idle** peer |
| `/fork` | the entire conversation | ~$1 | new agent |
| `/spawn` | **none** | one spawn | **new** agent |

The bottom row is the hole. `/spawn` is the only way to start a *fresh*
agent — no inherited conversation, no inherited bill — and it is the one
that arrives blind.

## The change

When `/spawn <agent> <prompt>` is typed from a live pane and that pane
has a transcript, the new agent's opening turn becomes the operator's
words **plus** the same three things `@peer` sends:

1. **Provenance of place, not author.** "The operator started you from
   inside another conversation — tab `X` (opus)." Tagged as though the
   source *agent* were delegating, a spawned agent reads it as
   peer-to-peer orders and skews toward pleasing the peer rather than the
   operator. The truthful framing is that the operator typed this while
   standing somewhere else.
2. **A tail of that conversation** — a bounded window of the source
   transcript, `TEASER_MAX_TURNS` (3) turns, assembled from the log. A
   disk read and no model call, which is what lets the design push a
   pointer and let the agent pull the rest.
3. **A pointer to the rest.** `aegis_read_peer("X")` returns a much wider
   window of the source, live. The wording puts the burden on answering
   *without* reading, for the reason `@peer`'s does: "verify this test"
   is complete-seeming English, nothing in it signals a missing referent,
   and noticing an absence is what context windows are worst at. The
   window's honest header ("last 3 of 143 turns") is included precisely
   to turn an undetectable absence into a legible one.

The one place the wording inverts `@peer` is the ending. `@peer` says
*answer this, do not start long work* — it is spending an idle peer's
turn, and a peer that wanders off is a peer that stopped being idle for
someone else's task. A spawn is the opposite: the whole point of paying
for a new agent is that it goes and does the thing. So the composed body
tells it to do the work, and to `aegis_handoff` back to the source when
the result matters there.

## Where it does *not* apply

- **`/spawn` with no prompt.** Nothing was asked, so there is nothing for
  a referent to be missing from.
- **A source with no readable tail** — a pane whose first input is the
  `/spawn` itself, a bridge with no `read_peer`, a damaged log. The
  preamble rides on the tail: with no tail the agent gets the bare
  prompt, because provenance pointing at a transcript nobody can read
  buys the new agent one failed tool call and a paragraph of confusion.
- **`aegis_spawn` over MCP.** An agent calling it is *told* to write a
  self-contained payload, and it has the context to do so. This is about
  the human typing three words into a box.

## Implementation

- `aegis.peer.compose_spawn(source, slug, prompt, tail, header)` — pure
  text, sibling to `compose`. Takes the tail as strings rather than a
  `Window` because it is fed across the `read_peer` dict seam.
- `aegis.commands.builtins.core._spawn_opening` — resolves the source
  slug from `bridge.list_sessions()`, pulls the window with
  `bridge.read_peer(handle, TEASER_MAX_TURNS)`, composes. Every failure
  path returns the bare prompt.

`read_peer` is reused deliberately rather than growing a second bridge
method. Both `AppBridge` implementations already have it and both are
already correct; the TUI's `peer_ask` silently shipped a degraded path
three times by being a second copy of a call `SessionManager` got right,
and that comment is still in `tui/app.py` for a reason.

The knock-on is that the window is assembled at `READ_BUDGET_TOKENS`
(24k) rather than the teaser's 2k — bounded to 3 turns either way, so
what differs is how hard those 3 turns are trimmed. For an agent that is
about to *act* on the referent rather than answer one question about it,
the wider window is the side to err on.
