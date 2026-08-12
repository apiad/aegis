# Telling an agent it was renamed

**Status:** designed 2026-08-12; not yet implemented.
**Scope:** `AgentSession` (one field, one method, one hook in `_run_turn`),
the two `rename_handle` implementations, and one new sender tag. No new MCP
tool, no new config, no change to any tool's signature except one keyword
argument on `rename_handle`.

## The problem

An agent is never told when the operator renames it. No message announces
it, and its system prompt still carries the handle it was born with. So the
handle an agent believes is its own can be two renames stale, and nothing in
the system says a word.

That belief is load-bearing. `from_handle` is a *parameter* on
`aegis_monitor`, `aegis_enqueue`, `aegis_remind` and `aegis_handoff`, passed
back by convention from the primer — so an agent working from a stale name
addresses its callbacks to a handle no session answers to. The monitor arms
fine, polls fine, trips, and delivers the wake to nobody.

Observed 2026-08-12, in this exact shape:

| time | event |
|---|---|
| 13:22:00 | agent renames itself → `idle-drain-hang` (its own call, so it knows) |
| **13:22:14** | **operator renames it → `aegis-work`** — agent not told |
| 13:27:31 | agent arms a monitor as `idle-drain-hang` — stale by 5 minutes |
| 13:31:54 | agent arms a second monitor, same dead handle |

Both monitors watched for minutes in silence. The wake would have gone
nowhere. The agent discovered the rename only by calling
`aegis_list_sessions` for an unrelated reason, eleven minutes later.

Commit `3686083` made `start_monitor` refuse a handle no session answers to,
which converts this from a silent hang into a loud error. That is a
backstop, not a fix: it tells the agent *at the moment it fails* rather than
*at the moment it changed*, and it only covers monitors —
`aegis_enqueue` / `aegis_remind` / `aegis_handoff` still accept a stale
handle today.

## What this is not

**This is not the root fix, and should not be mistaken for it.**
`TASKS.md:245` files *"Per-session MCP identity — make `from_handle` a
transport fact"*: mint a per-session token at spawn, inject it with the MCP
config, and resolve `from_handle` server-side instead of trusting the
argument. That would make a stale handle structurally impossible, and would
also close the `aegis_claim` / `aegis_close` / `aegis_loop_stop` trust gaps
and the comms ledger's `(unattributed)` hole.

The two are complementary, not alternatives. Per-session identity makes
*routing* trustworthy; this makes the agent's *self-knowledge* true — which
still matters after C lands, because an agent names itself in prose, tells
peers where to reach it, and writes its handle into artifacts.

Building this does not make the filed task cheaper or harder.

## Who counts as "someone else"

The rule: announce when someone **other than the session itself** changes
its handle.

Half of that is not implementable yet, and for the same reason task C
exists. `aegis_rename(old_handle, new_handle)` takes no `from_handle`, and
`AegisMCP` is co-resident and shared — every session reaches the same HTTP
port — so given a rename arriving over MCP there is no way to tell whether
an agent renamed *itself* or renamed *a peer*.

So the actor is declared by the call site, and only two are distinguishable:

| call site | actor | announce? |
|---|---|---|
| `/rename` in the TUI (`tui/pane.py:1906`) | operator | **yes** |
| `/rename` in the web client (`web/wssession.py:180`) | operator | **yes** |
| `rename_handle` RPC from the web client (`web/wssession.py:305`) | operator | **yes** |
| `aegis_rename` MCP tool (`mcp/server.py`) | an agent — self or peer, indistinguishable | **no** |

`rename_handle(old, new, title=None)` gains one keyword, `by: str =
"agent"`; **all three** operator call sites pass `by="operator"`. Defaulting
to `"agent"` means a caller that forgets stays silent — the failure mode is
a missing notice, not a false one.

The web client has *two* operator paths, not one: the slash-command dispatch
at `wssession.py:180` and a direct `rename_handle` RPC at `wssession.py:305`.
Both reach `SessionManager.rename_handle`. Missing the second would leave
the web client renaming silently, which is the same bug in a different
frontend — and this repo has already shipped one rename defect that lived in
exactly one of two paths (`fb262d7`).

**Known gap:** an agent renaming a *peer* stays silent, exactly as today.
Faking an attribution would be worse than an honest hole. When task C lands
and the caller is a transport fact, the rule tightens to *"announce unless
the resolved caller is the target"*, which picks up the peer case for free
and lets the `by=` keyword be dropped.

## Mechanism

`AgentSession` grows one field and one method:

```python
self._pending_notices: list[InboxMessage] = []

def note_rename(self, old: str, new: str, *, by: str) -> None
```

Both `rename_handle` implementations — `AegisApp` (`tui/app.py:2012`) and
`SessionManager` (`core/manager.py:488`) — call it **after** `handle = new`,
so the notice states the new name as current fact.

It is consumed at the top of `_run_turn` (`core/session.py:436`), the
unified path every prompted turn goes through whatever woke it: a typed
message, an inbox batch, a monitor callback, a loop tick, a reminder. The
notice is rendered with the existing `_render_batch` and prepended to that
turn's text, then cleared. One turn, one delivery, never repeated.

Three consequences, each deliberate:

- **Zero cost at rest.** Renaming an idle agent starts nothing. The notice
  waits until the agent next has a reason to think — the only moment it
  could act on a stale handle. Renaming five tabs while tidying up bills
  nothing.
- **`_drain_unsolicited_turn` bypasses `_run_turn`** on purpose (it skips
  hooks and `send()` because the harness is mid-stream), so a notice does
  not ride an unsolicited drain. Correct: that drain is the harness talking
  to itself, not the agent acting on its identity. The notice waits for the
  next real turn.
- **Emitted through `_emit_dispatch`** so it lands in the transcript and the
  session log, not only in the prompt. Without that, the one artifact you
  would want when asking *"why did it use the old name"* is invisible.

### Why not the inbox

The obvious implementation — deliver an `InboxMessage` through
`InboxRouter` — is wrong twice over.

`AgentSession.deliver` wakes an idle session into a full turn
(`core/session.py:371-379`), so every rename would bill an LLM turn to tell
an agent its own name. And `send()` does not drain `_inbox_buffer`
(`core/session.py:320-326`), so a notice buffered without waking would fire
as a *follow-up* turn — after the agent had already answered under the wrong
name. Late and billed. The preamble is both earlier and free.

## The message

Rendered through the existing header machinery, so it reads like every other
substrate message. One new sender tag, `substrate`, in `queue/schema.py` —
the primer already names it as a message source ("queue callbacks, peer
handoffs, and the substrate").

```
> from substrate · 2026-08-12T13:22:14Z

You were renamed by the operator: `idle-drain-hang` → `aegis-work`.

Use `aegis-work` as your handle from now on — as `from_handle` on
aegis_monitor / aegis_enqueue / aegis_remind / aegis_handoff, and when
you tell a peer where to reach you. The old handle no longer routes:
anything addressed to it is delivered to nobody.
```

The last sentence carries the weight. "You were renamed" invites a shrug;
naming the consequence is what makes an agent go correct its `from_handle`
rather than note the fact and move on.

## Tests

The property that matters most is the negative one — that this costs nothing
at rest. It is also the one a later "simplification" back into `deliver()`
would break, so it is written first:

- **an operator rename on an idle session starts no turn** — state stays
  `ready`, and the fake harness records no send.
- **the notice rides the next turn** — rename, then `send("hello")`, and
  assert on the text *the harness received*, not on the session's internal
  list. Asserting the substrate, not the entry point.
- **it fires once** — a second turn carries no notice.
- **both rename implementations raise it** — parametrised over `AegisApp`
  and `SessionManager`. These two have already drifted apart once
  (`fb262d7`, where only one migrated the monitor and reminder planes),
  which is precisely why this is one test over both rather than two tests.
- **all three operator call sites pass `by="operator"`** — including the web
  client's direct `rename_handle` RPC, which is the one an implementer is
  most likely to miss.
- **`by="agent"` stays silent** — an MCP self-rename produces no notice.
- **a rename mid-turn** does not disturb the running turn and lands on the
  next one.

## Out of scope

- **Title changes** (`aegis_title`, `/title`) — display only. The handle
  stays the identity for routing, inbox and log id, so nothing goes stale.
- **Self-renames** — `aegis_rename` already returns `{ok, old, new}` to the
  caller.
- **Peer renames** — blocked on task C, as above.
- **`aegis_enqueue` / `aegis_remind` / `aegis_handoff` handle validation** —
  the same backstop `3686083` added to monitors would fit all three, but it
  is a separate change with its own blast radius, and task C subsumes it.
