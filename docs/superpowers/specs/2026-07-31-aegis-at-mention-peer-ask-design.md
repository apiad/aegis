# `@peer` — asking an idle agent, from where you're standing

*Status: implemented 2026-07-31 (VS1–VS4). Three TUI wiring changes
outstanding in `tui/app.py` + `tui/pane.py`, handed to `btw-rendering`
which holds those files; until they land, `@` works on the web client and
is delivered as literal text in the TUI.*

*One departure from this spec, found while mapping the seams: **`@handle …`
is sugar for a slash command**, rewritten in `classify_input` into
`/peer handle …`. The spec projected "a `peer_ask` route in the two input
seams"; there is no new route at all — the existing dispatcher, the
`CommandResult.effect` channel, the palette and the web seam carry it
unchanged, `wssession.py` needs nothing, and `tui/pane.py`'s gate widens by
four characters. `--cc` rather than `@@peer`, because `@@` has to be the
literal-`@` escape mirroring `//`.*

## The problem

You have ten tabs open. Eight are idle. Each idle tab holds a warm
context that cost real money to build and is currently returning
nothing.

Getting an answer out of one of them costs more than it should. You
switch tabs, losing your place. You retype the question with all its
context, because the peer cannot see what you were looking at. Or you
delegate — and pay the cost TASKS.md names as the largest one in the
system:

> the worker is a fresh agent with no context — write the payload as a
> self-contained prompt with everything it needs

`/fork` (shipped 2026-07-31) solves that by carrying the *entire*
conversation, at ~$1 and a new agent. `/btw` (shipped the same day)
solves the cheap end by answering from your own transcript tail for
~$0.08, with no agent at all. Between them is a hole:

| | context carried | cost | target |
|---|---|---|---|
| `aegis_handoff` | none — you retype it | free | live peer |
| **`@peer`** | **a bounded slice of where you stand** | **~one peer turn** | **idle live peer** |
| `/fork` | the entire conversation | ~$1 | new agent |

## What it is

> **`@peer` is a `/btw` whose window comes from your conversation and
> whose `generate()` is a real agent's real turn.**

You type `@lucid-knuth is this schema right?` into tab A. The peer gets
your question, a free teaser of where you're standing, and a tool it can
call to read more. It answers. The answer lands as a real turn in *its*
transcript and as a transient block in *yours*.

## Why idle-only

**A busy peer is refused.** Not as a v1 shortcut — as the feature's
actual domain.

The whole economic case is extracting value from a warm context that is
currently producing nothing. A busy peer inverts that: it is already
producing value, and cutting in costs you the exact thing you were
trying to get. Interrupting it to answer a side question is the expense
`@peer` exists to avoid.

Three consequences follow, and the first is the best property in the
design.

**`@peer` is legal while *A* is mid-turn.** Same reasoning that makes
`/btw` legal mid-turn: it never touches A's harness session, only B's.
The guard is on the target, never the source. This is the killer case —
you are eleven minutes into a long turn in tab A, you cannot say anything
useful to A, and you spend the dead time asking an idle peer.

**You should never hit the refusal.** The `@` drop-up renders peer state
in the dim right column. `Completion.detail` already exists
(`commands/__init__.py:122`) and `SessionInfo` already carries `state`
(`"ready" | "working" | "error"`) and `active`
(`mcp/bridge.py:8`). Idle peers are selectable; busy peers render greyed
with `busy`. The constraint becomes visible at type-time instead of a
rejection at send-time — a rule rather than a wall.

**The peer must answer, not embark.** B is idle when you send, but
nothing stops it deciding your question warrants twenty minutes of real
work, at which point "synchronous" is a lie and tab A hangs. The
instruction carries a scope clause (below). B replying *"this is a task,
not a question"* is B telling you to reach for `/enqueue` instead —
a useful signal, not a failure.

## The gesture

`@` becomes a second trigger character over machinery that exists.

- `classify_input` (`commands/__init__.py:104`) gains an `@` branch
  beside `//` and `/`, returning `("peer_ask", text)`. `@@` escapes to a
  literal `@`, mirroring `//`.
- `complete(text, bridge)` (`commands/__init__.py:146`) currently returns
  empty for anything not starting with `/` (line 151). It gains an `@`
  branch that fuzzy-ranks `bridge.list_sessions()`, with `detail`
  carrying `agent_slug` plus `busy` when `state != "ready"`, and
  `insert=f"@{handle} "`.
- Both palette seams pick this up for free: the TUI calls `complete()` at
  `tui/pane.py:1307` and `:1342`, the web at `web/wssession.py:204`.

Syntax is **line-prefix only**: `@handle <rest of line>`. A mid-sentence
`@handle` is plain text. One target per line in v1.

Refusal is explicit and names the alternative:

    @lucid-knuth is mid-turn. Wait, or /enqueue it.

Unknown handle, self-target, and a non-live handle refuse the same way.

## What travels: a teaser, not a summary

The naive design pays a `generate()` call on the sender's side to
summarize tab A before sending. That taxes every `@peer` — including the
many whose question was self-contained anyway — at ~$0.08 a send.

Instead: **push a free teaser, let the peer pull.**

`btw/window.assemble(replay, *, max_turns, budget_tokens, item_chars)`
(`btw/window.py:125`) fills a window backwards from the newest event to a
bound and returns a `Window` with `.text` and an honest `.header`
("6 of 143 events, 2 truncated"). Called at a **small** budget it costs
nothing but a log read — no model, no API call. That yields:

- the tail of what tab A is actually doing, and
- a header stating how much the peer is *not* seeing.

This is the part that makes pulling work. The failure mode we are
designing against is not laziness — it is that the model cannot detect
the gap. *"Look at this and tell me if it's right"* is complete-seeming
English; nothing in it signals a missing referent, and noticing an
absence is the one thing context windows are worst at. A visible
boundary ("you are seeing 6 of 143") converts an undetectable absence
into a legible one.

Resolving A's transcript is trivial because A is live:
`SessionManager.get(handle).log_id` (used exactly this way at
`core/manager.py`'s `side_note`), then
`state.session_log.replay_events(state_dir, log_id)` off the event loop
via `asyncio.to_thread` — the same three lines `side_note_for`
(`btw/__init__.py`) already runs. *(The `SessionMeta` scan needed to
resolve a **closed** session's log does not arise: `@` only targets live
peers.)*

## The prompt

Two things the wording must get right.

**Provenance of place, not author.** The message must not read as *agent
A asking*. Tagged `from agent:<handle>`, the peer reads it as
peer-to-peer delegation and skews autonomous — it goes and *does*
things. The truthful framing is that the **operator** asked, while
standing in conversation X. This gets a new sender tag beside
`sender_agent` / `sender_queue` / `sender_user`
(`queue/schema.py:19–52`):

```python
def sender_operator_at(handle: str) -> str:
    """The operator asked, from inside another conversation."""
    return f"operator@{handle}"
```

rendering as `> from operator@aegis-at-mentions · <ts>`.

**Default to pull.** *"If you need to, call `aegis_read_peer`"* biases
toward not calling. Put the burden on answering-without-reading instead.
The asymmetry backs this: a wasted `aegis_read_peer` costs a tool call,
while a confident answer to a misunderstood question costs trust in the
whole feature.

The composed body, mirroring `btw._PREAMBLE`:

```
The operator typed this from inside another conversation — tab
`<source>` (`<agent_slug>`) — and it probably refers to what is
happening there. Below is its recent tail: <window.header>.

Read the fuller conversation with aegis_read_peer("<source>") before
answering, unless the question is plainly self-contained.

Answer it. Do not start long work — if this needs real work rather than
an answer, say so and stop, and the operator will delegate it properly.

--- tail of <source> ---
<window.text>
--- end ---

The operator's question: <prompt>
```

## Where the answer lands

**In B's transcript, as a real turn.** Not negotiable, and not a matter
of taste: if B's log held a question with no answer, every window later
assembled from it would be corrupt — B's own `/btw`, any `/fork` of B,
any `aegis_read_peer` a third agent runs against B.

**In A's pane, as a transient block.** If the answer landed *only* in B's
tab, `@peer` would be strictly worse than switching tabs: you fired a
question into another room and now have to go find it. So A also gets a
block, through the mechanism `/btw` shipped — mounted into the pane's
`_history` via `_mount_block`, **never appended to A's session log**
(`tui/pane.py:1380–1393`). A's model never sees it and never pays for
it; you see it when you scroll back.

Because the target is idle by construction, this is synchronous: one
send, one turn, a finished block. No resolving placeholder, no new async
surface in the pane.

**On overrun**, tab A stops waiting and says so. B's turn continues and
lands in B's own transcript, so nothing is lost — you go read it there.
Timeout is a constant, defaulted to the same order as `aegis_delegate`'s
`timeout_s`.

## Who decides whether A hears about it

**You do, at send time.** Not B at reply time.

The tempting design prompts B with *"handoff to A if relevant."* Three
reasons it is wrong:

1. **B cannot judge.** You would be asking B whether something is
   relevant to A's work when B has, at best, a summarized window of A. It
   will guess, and it will guess *yes* — models are agreeable — so you
   get a relay into A most of the time. That is the expensive default
   arriving through the back door.
2. **A handoff into A is not a light touch.** It is a real turn: A pays
   tokens, and if A is mid-task it lands at A's next boundary and derails
   it. Heavy instrument for "here is an answer to a side question."
3. **You already know.** At the moment you type it you know whether this
   is *"quick, is the build green"* (yours alone) or *"ask lucid-knuth
   what schema it settled on, we need it here"* (A must have it). That
   knowledge exists at send time and is gone by reply time.

So: `@peer` → the answer is yours, transient, A never sees it. `@@peer`
is taken by the literal-escape, so the CC modifier is a trailing flag:
`@peer --cc <question>` delivers B's answer into A as a real user turn as
well.

The concession: there *is* a case for B reaching back — when B learns
something that invalidates A's work ("the file you are editing, I just
rewrote"). But that is not reply-routing, it is what `aegis_handoff`
already is, and B can call it on its own judgment. One line in the prompt
saying so is harmless; making it the reply path is wrong.

## `aegis_read_peer` — the pull tool

```
aegis_read_peer(handle: str, turns: int = 12) -> {text, header, ok, error}
```

A legible wrapper over three things that exist: `SessionManager.get`
→ `.log_id`, `replay_events`, `assemble`. Returns the window text plus
its honest header.

**This unlocks no new capability.** Transcripts are plain JSONL at
`.aegis/state/sessions/<log_id>.jsonl` inside the project root, and every
agent has `Read` and `Bash` today. What is missing is *addressing*: the
log id is `<timestamp>-<birth handle>`, minted at spawn and never changed
(`state/session_log.py:83`), so an agent cannot get from a peer's current
handle to its file without knowing that. `aegis_read_peer` is
discoverability, not permission — which is also why the privacy question
does not bite here: single operator, shared filesystem, already readable.

The payoff generalises past `@peer`. Every inbox message today carries
`> from agent:<handle>` — a name with nothing behind it. With this tool,
that header becomes resolvable, and handoffs, queue callbacks and group
broadcasts *all* become inspectable at their source. `@peer` is the
operator-facing gesture onto a substrate change that improves five
coordination primitives already shipped.

## Architecture

**What exists and is reused.** `btw/window.assemble` + `Window`;
`replay_events` / `parse_log_id` / `session_log_path`;
`AgentSession.add_event_observer` / `add_state_observer` / `deliver`
(`core/session.py:149,153,254`); `InboxRouter` delivery and
`render_inbox_header`; `Completion` / `complete` / `CommandPalette`;
`_mount_block` and the `effect` channel; `render_side_note` as the
template for `render_peer_answer` (`render.py:247`).

**What is new.**

1. `AppBridge.peer_ask(from_handle, target, prompt, cc=False)` — a
   sibling of `side_note` (`mcp/bridge.py:96`), returning a
   `PeerAnswer` shaped like `SideNote` (answer / header / model /
   duration_ms / cost_usd / ok / error). Three implementations, as
   `side_note` has: `SessionManager`, `AegisApp` (`tui/app.py:1444`), and
   an explicit refusal on `RemoteSessionManager` (the transcripts live on
   the server), plus the conformance test in `tests/test_mcp_bridge.py`.

2. **`session_send_and_await` — the real work.** `runner.py:442` reaches
   for this on the bridge via `getattr(..., None)`; **no production class
   defines it**, and it appears only as a test fake
   (`tests/test_dsl_mcp.py:17`, `tests/test_dsl_durability.py:19`). So
   `WorkflowEngine.send()` (`workflow/engine.py:355`) against a real
   bridge silently falls through to fire-and-forget and returns `""`
   today. `@peer` is the first real implementation: register a one-shot
   event observer on the target `AgentSession`, deliver, await the
   terminating `Result`, return the coalesced final assistant text (via
   `render.coalesce_chunks`). Un-stubbing `engine.send` is a free
   side effect worth calling out.

3. `aegis_read_peer` MCP tool + its `AppBridge` method.

4. `sender_operator_at` in `queue/schema.py`.

5. The `@` branches in `classify_input` and `complete`, and a
   `peer_ask` route in the two input seams that already dispatch
   commands.

**The build is dominated by (2).** Everything else is assembly.

## Failure modes

Best-effort by contract, exactly as `side_note` is: *"a side question must
never be able to disturb the conversation it sits beside."* Every failure
returns a `PeerAnswer` with `ok=False` and a reason.

- target unknown / not live / self → refuse before any delivery
- target `state != "ready"` → refuse, naming `/enqueue`
- target has no persisted transcript → send with no teaser, say so in the
  block
- B raises / dies mid-turn → `ok=False`, A's block shows the reason
- timeout → A's block says to go read B's tab
- log unreadable → send with no teaser rather than failing the ask

## Testing

TDD per the repo convention, hermetic first:

- `classify_input` / `complete` branches, including `@@` escape and
  `busy` detail — pure, no fakes
- refusal matrix (unknown / self / busy / not-live), one test each
- teaser assembly at a small budget: header honest, text non-empty,
  **no model call made** — assert on the driver being untouched, since
  the entire cost argument rests on it
- `session_send_and_await` against a fake `AgentSession`: returns the
  final assistant text, and returns it *once* (observer detached)
- A's log is **not** appended to — assert against the file on disk, not
  the pane; this is the property `/btw` earned and the one most likely to
  regress
- `--cc` delivers to A; bare `@peer` does not
- bridge conformance across all three implementations
- one live test behind the `live` marker: real `claude` peer, real turn,
  answer captured

## Deferred

- **Multicast** (`@a @b <q>`). More attractive under idle-only — "poll
  every free peer, one block, N answers" — but it changes block layout
  and the timeout story. v2.
- **Clickable mentions in agent output** — when an agent *writes*
  `@handle`, render it as jump-to-tab. Independent, cheap, no overlap
  with this.
- **Reading closed sessions.** `aegis_read_peer` is live-only in v1; a
  closed peer needs the `SessionMeta` scan to resolve current handle →
  log id.
- **Web parity for the block.** The `effect` channel carries it as JSON
  the way `/btw` does, but the web treatment is its own slice.
- **Making all inbox provenance resolvable.** The generalisation named
  above — worth its own spec once `aegis_read_peer` has been used in
  anger.

## Open questions

- **The teaser budget.** `/btw` measured the window as most of the bill
  at 32k. The teaser wants to be far smaller — enough to place the peer,
  not enough to answer from. A number wants measuring, not guessing;
  start small and watch how often peers pull.
- **Does the peer actually pull?** The whole pull design rests on it. The
  live test should record the pull rate, and if it is low the fallback is
  not more prompt-wording but a `needs_more`-shaped structured field, the
  way `/btw` converted the same guess into a signal.
