---
title: /btw — a side note that doesn't cost a conversation
date: 2026-07-31
status: design
---

# `/btw` — a side note that doesn't cost a conversation

You are mid-task and a question surfaces that is adjacent to the work:
*wait, which of these two paths does the resume flow actually take?* It is
not worth derailing the turn, it is not worth a new tab, and by the time
you have typed it into a fresh agent with enough context to be answerable
you have lost the thread you were pulling.

`/btw <prompt>` answers it inline and disappears.

This spec covers `/btw` only. Its sibling `/fork` — a real fork into a
permanent tab — is specced in
`2026-07-30-aegis-conversation-fork-design.md`. The two were designed
together on the assumption they were one primitive with two dispositions.
**They are not**, and the evidence for that is the substance of this
document.

## What we assumed, and what it cost to find out

The obvious design: `/btw` is a fork that renders inline and gets killed.
Claude Code's own `/btw` works roughly that way — one API call, all tools
denied, rendered inline, never written back to the conversation.

aegis cannot do that in-process. Claude Code owns the API call; aegis
drives a CLI. So `/btw`-as-fork means
`claude --fork-session --resume <sid> -p "<question>"` — a real
subprocess, with a real session file that `claude` writes whatever aegis
wants.

Before designing around that, we measured it. Five probes on zion,
2026-07-31, against this workspace and a live conversation:

| probe | shape | latency | cost | outcome |
|---|---|---|---|---|
| **A** | fork of a **mid-turn** session, no primer/denial | 42.7s | **$1.38** | ❌ `error_during_execution`, `stop_reason: tool_use`, 4 turns, **no answer** |
| **D** | fork of a **cleanly-ended** session, primer + tool denial | 14.0s | $0.99 | ✅ correct, both halves |
| **E** | **cold** one-shot, no fork, primer + tool denial | 15.0s | $0.43 | ✅ correct; `Bash` confirmed unavailable |

Three findings, each of which killed a piece of the assumed design.

### 1. A mid-turn fork is genuinely torn

The ability to ask a side question *without stopping the main task* is the
entire premise. We argued it was safe: mid-turn, `claude` has not flushed
the in-flight turn, so a fork branches from the last completed turn —
stale, not torn.

That was wrong, and probe A is what it cost. `claude` appends each message
as it is produced, so the tail of a live session is
`assistant → tool_use` with **no matching `tool_result`**. A fork inherits
that dangling call and burns four turns trying to reconcile it before
failing outright. 42.7 seconds and $1.38 for no answer.

The fork spec's mid-turn refusal was right on first principles. The
exception we carved out for `/btw` was wrong, and it removed the one
property that made `/btw` worth having.

### 2. A fork does not inherit the parent's warm cache

This is the number the fork spec's closing open question asks for.

Probe D paid `cache_creation: 86,881` tokens — written at a **premium**
over base input — on top of `cache_read: 215,775`. A fork does not ride
the parent's cache; it builds its own from the shared prefix.

**~$1 per fork on a mid-size conversation, scaling with context.** And
`/btw` is most useful deep into a long session, which is exactly where it
is most expensive.

### 3. The ~15s floor is not the fork

Probe E did no forking at all and still took 15.0s and $0.43. That is
`claude` subprocess startup plus this workspace's `CLAUDE.md`, skills
list, and tool schemas — paid before the model reads the question.

Any *tool-capable* `/btw` pays that floor. The fork is not the expensive
part; **being a `claude` subprocess at all** is.

## The trilemma

Cheap, fast, tools — pick two.

| shape | tools | latency | cost | verdict |
|---|---|---|---|---|
| Fork of the parent | ✅ | 14s | ~$1.00 | Correct, and too expensive to fire without thinking |
| Cold subprocess + digest of recent turns | ✅ | 15s | ~$0.45 | **Strictly dominated** — no faster, and it *loses* full context |
| One-shot `generate()`, no tools | ❌ | ~2s | ~$0.01 | Cheap and instant; sees only the window aegis assembles |

The middle row is worth stating explicitly so nobody re-proposes it: it
pays the subprocess floor *and* gives up the context inheritance that was
the only reason to pay it.

## The decision

**`/fork` stays a real fork.** $1 is cheap to hand a peer an entire
conversation. You pay it deliberately and rarely, and you get an agent
that can go and look at things.

**`/btw` is the `generate()` one-shot.** At ~2s and a cent it is something
you fire without thinking, which is the whole premise. At 14s and a dollar
you would hesitate every time — and a side question you hesitate over is
one you simply do not ask.

It also converges with `2026-07-30-aegis-session-titles-design.md`: both
build one seam instead of two.

**What this trades away, stated plainly.** No tools means `/btw` answers
from the conversation window only. It cannot go read a file you have not
already looked at. That was an explicit ask, and this is the design
decision that gives it up. §*The window is not enough* is how we get a
usable fraction of it back.

**The unexpected payoff.** `/btw` never touches the harness session — it
reads aegis's own log and makes an independent call. So **it works
mid-turn after all.** The property the fork approach destroyed comes back
for free, having taken the long way round.

## Shape

### Surface

```
/btw <prompt>
```

A TUI slash command. `prompt` is **required** — a bare `/btw` is a typo,
not a request, and should say so.

**No MCP verb.** `/btw` is for the human at the keyboard, mid-thought. An
agent that wants a side answer can simply think, or `aegis_enqueue` if it
wants a real one. Adding `aegis_btw` would be a worse `Task` tool.

Works while the pane is `working`. This is the point of it.

### The window

`/btw` reads this session's own log — `.aegis/state/sessions/<log_id>.jsonl`
via `replay_events` — and assembles a bounded window.

**Boundary.** Walk backwards counting `Result` events; those terminate
turns (`events.py:101`, and `replay_events` already scans backwards
against `Result` for its interruption check). Ten of them is ten turns.

**What goes in:**

| event | treatment |
|---|---|
| `UserMessage` | verbatim — *see prerequisite* |
| `AssistantText` | verbatim, after `coalesce_chunks` |
| `ToolUse` | `name` + `summary` (already a one-liner via `_TOOL_SUMMARY_KEY`) |
| `ToolResult` | `.text` truncated **per item** to ~500 chars, marked |
| `AgentPlan` | compact form |

**What stays out:** `AssistantThinking` — `claude` redacts the text
anyway, so it is the worst tokens-per-insight in the log. Also
`SystemInit`, `ContextUpdate`, `SessionMeta`, `Unknown`.

**Budget.** Fill **backwards from newest** until ~10k tokens, then stop.
Newest-first means you always keep what the question is actually about;
truncating from the front would drop the turn that prompted the question.
Estimate at `chars / 4` — aegis should not grow a tokenizer dependency for
this, and the budget is a guardrail, not an accounting boundary.

**Honest header.** The window states what it dropped, to the model and in
the rendered footer: *"last 8 of 47 turns · 3 tool results truncated"*.
Both the model and the reader should know they are seeing a slice. This
is the same principle as the `⚠ damaged record(s) skipped` marker in
`replay_blocks` — a silently shortened transcript reads as a conversation
that was always this short.

### The call

One `generate()` against the seam from the session-titles spec —
`supports_oneshot`, `generate(agent, cwd, schema, …)`, with the model
chosen by the `text_generation:` config key.

```python
class BtwAnswer(BaseModel):
    answer: str
    needs_more: bool = False
```

Cost lands wherever `text_generation:` points: ~$0.01 on Haiku, ~$0.15 if
it is left on Opus. That knob does real work here, so `/btw` should warn
**once per session** when it is unset rather than quietly billing Opus
rates for a side note.

### The window is not enough

`needs_more` is how the capability we traded away comes back as a signal
instead of a guess. When the model sets it, the side note renders:

> answered from the last 8 turns — `/fork` if you want it to actually go look.

`/btw` stays cheap by default and **tells you when cheap was not enough**,
rather than confidently answering from a window that did not contain the
answer. A wrong-but-fluent side note is worse than no side note; this is
the mitigation.

### The render — transient

**Decided 2026-07-31: a side note does not survive a reload.**

The side note mounts into the transcript with its own visual treatment
(distinct from both `render_user_line` and agent output — it is neither),
carrying a footer with model, latency, cost, and the window header above.
It is a paid call and the price should be visible.

Transience has a precise meaning here, and the split is clean:

- **It goes into `_history`** (the pane's in-memory `BlockRecord` list),
  so scrolling up and back down keeps it, and `REPLAY_TAIL` windowing
  handles it like any other block.
- **It is never appended to the session log.** No `append_event`, no new
  event type, no codec entry, no `render_event` branch.

`_history` is memory; the log is disk. `/btw` lives in the first and never
reaches the second.

Two consequences worth naming:

1. **It is fully decoupled from the event-schema work.** `/btw` needs no
   typed event of its own. The only schema work it depends on is the
   prerequisite below, which is somebody else's bug fix.
2. **Side notes do not compound.** Because the answer never enters the
   log, it never enters the window a *later* `/btw` assembles. Ask five
   side questions and the fifth still sees ten turns of real
   conversation, not four of its own prior musings. This falls out of the
   transience decision for free, and it is the better half of it.

## Prerequisite — `UserMessage` as a typed event

A window without the user's turns is the agent talking to itself. This
prerequisite turned out to be much smaller — and much more valuable —
than it first looked.

The user's turns **are already persisted**. `claude` is spawned with
`--replay-user-messages`, so `{"type":"user", …}` records land in every
log. But `events.py` has no typed event for them, so they fall through the
parser's catch-all into `Unknown(raw=…)`, and two functions then drop
`Unknown` on the floor: `render.py:203` and `renders_to_nothing()`
(`render.py:146`).

Which means **`Ctrl+R` is broken today**, independently of `/btw`: the
live pane keeps user lines because `render_user_line` mounted the widgets
at send time, but every path that rebuilds from the log — `Ctrl+R`, a
restarted aegis, the web client's history, `aegis doctor` — loses them. A
reopened conversation reads as answers with no questions.

So the prerequisite is not *"start emitting user messages"* but
**"promote a stream record already being captured from `Unknown` to a
typed `UserMessage`"** — parser, codec, render branch. And it is
retroactive: every existing log already holds the raw blobs.

**This is tracked as its own work, as a bug fix rather than as a
`/btw` prerequisite** (handle `ctrl-r-replay`, 2026-07-31), together with
the separate `Ctrl+R` performance problem in `list_history`. `/btw` should
not block on the performance half — only on the typed event.

## Slices

Ordered so that each slice is independently valuable and nothing ships
gated on a spec that has not landed.

1. **`UserMessage`** *(prerequisite, owned elsewhere)* — typed event,
   codec, render branch, `renders_to_nothing` kept in step. Fixes
   `Ctrl+R` retroactively. Done when a log written *before* the change
   replays with its user turns.
2. **The window assembler.** Pure function, no LLM: `EventReplay` →
   `(text, header)`. Turn boundary, inclusion rules, per-item truncation,
   backwards budget fill. Fully testable against fixture logs and the real
   693MB corpus. Done when a 47-turn log yields a ≤10k-token window whose
   header honestly reports what it dropped.
3. **`generate()` seam** — shared with session-titles; whichever spec
   reaches it first builds it.
4. **`/btw` end to end.** Command parsing, the call, `BtwAnswer`,
   transient render, the `needs_more` footer, the unset-`text_generation:`
   warning. Done when a side question fired **mid-turn** answers correctly
   from the window and leaves nothing behind on reload.

Slice 2 is the real content and it needs no LLM, no driver work, and no
new events — it can be built and tested the moment slice 1 lands.

## Testing

- **The window assembler is the surface worth testing hard**, because it
  is pure and everything else is one API call. Fixture logs for: fewer
  than 10 turns available; a single turn larger than the whole budget
  (must still produce *something*, truncated and marked); a `ToolResult`
  of 200k chars; `AssistantThinking` present and excluded; chunk runs
  coalesced.
- **Newest-first is an invariant, not a detail.** Assert that a window
  built from an over-budget log contains the *last* turn and not the
  first. Getting this backwards produces a `/btw` that is confidently
  answering the wrong question.
- **Assert the log is untouched.** After a `/btw`, the session log must be
  byte-identical. This is the invariant transience rests on and it would
  break silently.
- **Assert it survives scroll but not reload.** In-memory persistence and
  on-disk absence are two different claims and both are the decision.
- Mid-turn behaviour: fire `/btw` while the pane is `working`, assert the
  window builds from what has been flushed and the harness session is
  never touched.
- The `generate()` call itself is `live`-marked. Hermetic suite runs
  `-m "not live"` — **never** `-k "not live"`, which matches `live` as a
  substring.

## Open questions

- **Is ten turns the right boundary, or is it a token budget wearing a
  turn costume?** The budget already truncates; the turn count may be
  redundant. Ship with both, log which one binds in practice, drop the
  loser.
- **Should `/btw` see the *rendered* transcript or the event stream?**
  This spec says events. The pane's `BlockRecord.payload` already holds a
  text rendering of every block for copy-paste, which is a cheaper source
  and closer to what the human sees. Worth probing in slice 2 — if the
  payloads are good enough, the assembler gets considerably smaller.
- **Does `needs_more` actually fire?** A model that answers confidently
  from an insufficient window is the failure mode this design accepts in
  exchange for being cheap. If `needs_more` turns out to be always-false
  in practice, it is decoration and the honest response is to say so, not
  to keep rendering it.

## Rejected alternatives

**Denylist the tools on a forked `/btw`.** Probe E confirmed denial
genuinely holds — `Bash` was unavailable, not merely unapproved. But the
forked agent still had `Skill`, `ToolSearch`, `SendMessage`, `TaskCreate`
and `Workflow`. **A denylist leaks every tool Anthropic adds next.** An
allowlist would be closed by construction; `generate()` with no tool
surface at all is closed by construction *and* cheaper. This is a
secondary argument for the chosen design, and a note for anyone specifying
tool restrictions elsewhere in aegis: prefer allowlists, and verify by
probe rather than assumption.

**A dismissible overlay rather than an inline block.** Claude Code renders
`/btw` as an overlay that vanishes on keypress. Inline-and-transient was
the explicit ask: you want to keep reading the answer while you work, and
you do not want it in the transcript tomorrow.
