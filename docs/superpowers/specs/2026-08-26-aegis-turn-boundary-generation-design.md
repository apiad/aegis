# Turn-boundary generation: the loop judge and the recap

> **Status:** design approved 2026-08-26, not yet implemented. Next step is
> an implementation plan under `docs/superpowers/plans/`.

Two features that are siblings rather than neighbours: both fire **one
structured-generation call at a turn boundary**, both read the same facts,
and both are best-effort by contract. They ship together because the
interesting part — knowing what a turn actually *did* — is shared.

- **The loop judge** replaces the agent's self-report as the thing that
  ends a `/loop`. Today the loop appends a coda asking the agent to call
  `aegis_loop_stop`; the judge takes that decision out of the tunnel.
- **The recap** puts one line at the end of a turn saying what just
  happened, and a short block on demand via `/recap`.

## The problem each one solves

### The loop stops for the wrong reasons

`LoopState.render()` (`src/aegis/core/loop.py:37`) appends a fixed coda to
every delivery:

> If this instruction is now fully satisfied, call
> `aegis_loop_stop(from_handle='{handle}', reason='<why>')` and stop.
> Otherwise continue.

This has two costs. The small one is tokens: the coda rides in-context on
every iteration of every loop. The large one is that **the agent deciding
whether the agent is done is the agent grading its own homework**, from
inside the tunnel it has been in for N turns.

The workspace has already paid for this. From `CLAUDE.md`:

> **Burn, 2026-07-30.** A loop said *"sigue hasta wire it up all on
> warden"*. I read "warden" as the terminating condition, wired its two ML
> rails to the new inference engine, verified them live, and called
> `aegis_loop_stop` at **iteration 1 of 20** — leaving the model-manager
> UI, the model download/load/unload API, and the docs unbuilt.

This is not an aegis-specific failure. [openai/codex#27352][codex-issue]
reports the same shape: *"Codex CLI can prematurely mark a turn as complete
after the assistant emits a commentary/progress message that promises a
next action."* Codex's own long-horizon guidance ([Run long horizon tasks
with Codex][codex-blog]) leans entirely on model coherence — 25 hours, 13M
tokens, plan→edit→test→observe→repair — with completion signalled by the
agent itself. **No terminal coding harness surveyed has an external judge.**
The self-report is the known weak point everywhere; this design is an edge,
not a catch-up.

The mirror failure matters as much: a loop that spins, producing turns that
read as productive while nothing lands, and burns to its cap. Nothing in
the transcript distinguishes that from progress. The facts do.

### There is no cheap way to see where a session stands

Coming back to one of ten tabs means scrolling. Claude Code shipped
`v2.1.114` **session recap** (April 2026) for exactly this: unfocus the
terminal for three minutes and a background summary is waiting when you
return, plus `/recap` on demand.

Their gating is the part worth *not* copying. Recaps fire on turn count
(≥3 turns, "never twice in a row"), and [anthropics/claude-code#56346][cc-issue]
reports the predictable result:

> When the user is in a conversation where nothing changes between turns
> (e.g. asking questions, reading output), identical recaps accumulate —
> sometimes 10+ in a row with the exact same content.

Closed stale, unfixed. The lesson is free: **gate on substrate change, not
on turns elapsed.**

## What already exists

Neither feature invents plumbing. The one-shot seam has two consumers
already and this design adds a third and fourth:

| Piece | Where | What it gives us |
|---|---|---|
| `HarnessDriver.generate_detailed(agent, cwd, schema, *instructions)` | `drivers/base.py:116` | one call, Pydantic in, `Generation` out, never raises |
| `supports_oneshot` | `drivers/base.py:77` | the honest decline for gemini / opencode / lovelaice |
| `parse_structured` | `drivers/oneshot.py:53` | tolerant parse for drivers that can't take a schema |
| `generation_agent(fallback, agents)` | `btw/__init__.py:62` | the `text_generation:` billing profile, and honesty when unset |
| `assemble(replay, …) -> Window` | `btw/window.py:125` | newest-first bounded window with an honest header |
| `titlegen.suggest_title` | `titlegen.py:46` | the precedent contract: every failure returns empty |
| `effect={"kind": …}` render path | `builtins/core.py:312` → `tui/pane.py:1484` | how a side note reaches the screen |

## Decisions

Four, settled before design:

1. **The judge outranks `aegis_loop_stop`.** The tool stays and keeps its
   identity gating (`mcp/server.py:1469`, gated on `verified_handle` since
   commit `be55c7f`), but calling it no longer reaps the loop — it records
   an *advisory reason* the judge reads. Leaving the tool authoritative
   would leave the 2026-07-30 burn exactly where it is.
2. **The instruction stays verbatim; the judge writes an addendum.** The
   verbatim rule in `2026-07-23-aegis-loop-command-design.md` is
   deliberate — the previous turn may have ended somewhere unhelpful, so
   the instruction has to be present in the turn that acts on it. A
   judge-authored *replacement* is the ideal vector for the narrowing
   failure `CLAUDE.md` warns about ("read them at their WIDEST"). The
   addendum says what remains and what not to redo; it never restates the
   goal.
3. **Judge failure means continue.** Best-effort like `/btw` and
   `titlegen`. The iteration cap bounds runaway; a failed API call must
   never silently end a night of work.
4. **The recap never enters the agent's context.** It is for the operator.
   Feeding it back would make every turn open by reading a summary of
   itself.

## Component 1 — `TurnDigest`

New package `src/aegis/digest/`. Owned by `AgentSession`: snapshot at turn
start, diff at turn end.

```
digest/
  models.py     # CommitLine, RepoDelta, TurnFacts  (frozen dataclasses)
  collect.py    # snapshot() / diff() — the impure half, git via to_thread
  render.py     # TurnFacts -> the text block a prompt embeds
```

`TurnFacts` carries:

- **commits** — per repo, `HEAD` before → `git log --oneline <base>..HEAD`
  after. The candidate roots are the ones the session already records:
  `AgentSession._fire_event` (`core/session.py:621`) resolves write tools
  through `write_target` (`repos/writes.py:26`) into `RepoTracker.record`
  (`repos/tracker.py:75`).
- **files written** — same source, same `(host, root)` keying.
- **plan delta** — tasks that reached `completed` during the turn, from
  `PlanTracker.snapshot(ts)` (`plan/tracker.py:120`).
- **assistant tail** — the turn's final assistant message.
- **duration**, and an `ok` / `error` pair.

### Rules it inherits rather than reinvents

- **Off-host means no git.** The session's `place` decides. A non-local
  root is reported unavailable, never resolved against the local disk —
  the same string names a different tree there, so a local `git log`
  returns a silently wrong answer rather than an error. Same reasoning as
  `Claim.host` and `render_shared.file_target`.
- **The digest never raises into the turn.** Any failure yields a
  `TurnFacts` with `error` set. A summary must not be able to disturb the
  conversation it summarises.
- **The assistant tail accumulates by `message_id`.** Assistant text is a
  token stream; overwriting on each `AssistantText` captures the last
  *chunk*. The queue already paid for this — a worker signing off with a
  full sentence reported back as its last two words. Follow
  `coalesce_chunks`'s run rule, and skip events carrying a
  `parent_tool_use_id`: a subagent's narration is not the turn's answer.
- **Git runs off the event loop**, via `asyncio.to_thread`, like
  `side_note_for` does for the 24MB transcript read.

The `git log` call is bounded (`--max-count`) so a turn that rebases a
hundred commits contributes a line, not a wall.

## Component 2 — the loop judge

New `src/aegis/core/loop_judge.py`.

```python
class LoopVerdict(BaseModel):
    verdict: Literal["continue", "done", "stuck"]
    reason: str
    addendum: str = ""     # ignored unless verdict == "continue"
```

### Where it fires

At the loop tier of `_chain_if_pending` (`core/session.py:769`) — the
lowest tier of all, below inbox, unsolicited drain and reminders, and
already gated on `_unsolicited_hold == 0` so it yields to an armed monitor.
The judge runs *before* `iteration` increments, so a `done` verdict does
not consume an iteration.

`_chain_if_pending` is synchronous and dispatches through
`asyncio.create_task`. The loop tier therefore becomes a task that awaits
the judge and then runs the turn, emitting `AgentState.working` **before**
the judge call so the session does not flicker idle while it thinks.

Two cases skip the judge entirely rather than paying for a call:

- **The cap is already reached.** `LoopState.exhausted()` is checked first,
  exactly as it is today (`session.py:770`). A capped loop stops without a
  verdict; there is nothing to decide.
- **The first delivery.** `arm_loop` (`session.py:430`) chains immediately
  when the session is idle, so `/loop <text>` starts working rather than
  waiting for a turn. At that point no turn has run under the instruction
  and there is nothing to judge — the first delivery is always the
  instruction verbatim, with no addendum.

### What it is given

- the loop instruction, verbatim
- `iteration N/max`
- the `TurnFacts` for the turn that just ended, rendered
- a `btw.window.assemble()` window of the recent conversation
- the agent's `aegis_loop_stop` reason, when it called one, presented as
  *the agent's own claim* — not as an instruction

### What happens to each verdict

| Verdict | Effect |
|---|---|
| `continue` | `iteration += 1`; deliver `LoopState.render()` = instruction verbatim + addendum, through the existing `sender_loop` inbox path (`queue/schema.py:58`) |
| `done` | `stop_loop(reason)` with the verdict's reason, so the chip and transcript say *why* |
| `stuck` | `stop_loop(reason)`, distinguished from `done` in the reason text |
| call failed | `continue`, with no addendum |

### Detecting `stuck`

A single turn's facts cannot tell a stuck loop from a thinking one. The
signal is *consecutive* turns with no commits, no writes and no plan
movement. `LoopState` grows a short ring of recent digests — a bounded
in-memory field on a session-scoped object, not a new store; a loop
already does not survive a restart by design.

The judge is *told* the streak rather than left to infer it, since the
count is a fact and inference is what this feature exists to remove.

### What is removed

`_CODA` (`core/loop.py:18`) is deleted, and `LoopState.render()` takes the
addendum instead of the handle. That is the per-iteration in-context tax
gone. `LoopService.arm/stop/status` (`queue/loop.py`) is unchanged;
`stop_loop` grows an advisory path that records the reason without reaping.

## Component 3 — the recap

New `src/aegis/recap/`. Two schemas, not one with optional fields — a
schema serving two masters degrades both:

```python
class TurnRecap(BaseModel):
    line: str            # one line, past tense, what this turn did

class SessionRecap(BaseModel):
    building: str        # what the session is working toward
    done: str            # what has landed
    remaining: str       # what is left
```

### Auto, at turn end — gated on movement

Fires only when `TurnFacts` shows the substrate moved: a commit, a file
written, or plan progress. A turn of questions and reads produces no
recap and costs nothing. Plus an identity guard: a line identical to the
previous one is dropped rather than rendered.

This is the direct answer to [#56346][cc-issue]. Turn-count gating is what
produces ten identical recaps; movement gating cannot, because ten
identical recaps require ten turns that each changed something and each
changed it the same way.

### `/recap` — the operator asked, so it always fires

Session-scoped: a full-budget `assemble()` window plus the session's
accumulated facts, rendered as a short block (building / done / remaining).
More verbose than the automatic line, because the use case is auditing a
two-hour session before opening a PR — not re-orienting after one turn.

### Rendering

Through the path `/btw` already uses: `CommandResult.effect={"kind":
"recap", …}` (`builtins/core.py:312`) applied in `tui/pane.py:1484`
alongside `side_note`, with a `render_recap` beside `render_side_note`
(`render.py:331`), and the web mirroring it.

`side_note` exists on **four** bridge surfaces — the `AppBridge` Protocol
(`mcp/bridge.py:112`), `SessionManager` (`core/manager.py:335`),
`AegisApp` (`tui/app.py:1716`) and `RemoteSessionManager`
(`tui/remote_manager.py:255`, which raises `RemoteUnsupportedError`).
`recap` needs all four. This is not boilerplate to skip: `read_peer` has a
test asserting both bridges take the same window knobs precisely because a
signature that drifts on one bridge breaks that frontend *and no other*,
which is the hardest kind of bug to see.

## Configuration

Both features reuse `text_generation:` (`config/yaml_loader.py:75`), which
already fails loud when it names an agent that does not exist. Measured
2026-07-31 on zion: the same one-shot costs $0.0044 on haiku. Both decline
quietly when the resolved driver lacks `supports_oneshot` — a real path
for gemini, opencode and lovelaice today, not a theoretical one.

Two boolean knobs, both defaulting on: `recap:` and `loop_judge:`.

**On cost.** The recap fires roughly once per *productive* turn — an order
of magnitude more traffic than `/btw`, which is a handful of calls a
session. The movement gate is what keeps that honest, and it is the reason
the gate is a correctness requirement rather than a nicety. The judge fires
once per loop iteration, which is bounded by the cap.

## Testing

TDD per repo convention: failing test first, commit per logical unit.

- **Digest** — diffs against a real temporary git repo with real commits.
  Not mocks: the thing under test is whether we read git correctly.
  Includes the off-host case asserting we report unavailable rather than
  probing the local disk.
- **Judge** — a table of (facts, window, advisory) → verdict through a
  fake driver returning canned `Generation`s. Explicit tests that a failed
  call *continues*, that `done` does not consume an iteration, and that
  the delivered body contains the instruction verbatim.
- **Recap gate** — including a **mutation check**: break the movement
  predicate on purpose and confirm read-only turns start producing recaps.
  Per `CLAUDE.md` §5, a gate that cannot fail is worth less than none,
  because it licenses shipping.
- **Live** — one round trip behind the existing `live` marker, real
  driver, real model, following `tests/test_mcp_live.py`. Note the marker
  rule: `-m "not live"`, never `-k`, which matches `live` as a substring.

## Out of scope

- **Persisting either across a restart.** A loop is session-scoped by
  design; auto-firing a restored loop means a cold TUI starts spending
  tokens at boot.
- **Merging the two generation calls** on turns where both fire. Cheaper,
  but couples a per-turn feature to a per-loop one. Available later if the
  double call shows up in the bill.
- **An away-trigger for the recap** (Claude Code's unfocus-3-minutes).
  aegis is multi-tab, so the natural analogue is tab switch-back rather
  than terminal focus. Worth doing; not in this slice.
- **Moving either into a plugin.** `PostTurnEvent`
  (`hooks/contexts.py`) carries only `(session, user_message,
  assistant_message, project_root)` — a plugin-shaped recap would be
  starved of exactly the facts this design is built on. Enriching that
  payload with `TurnFacts` is the seam that would let both move later,
  toward `2026-08-23-aegis-plugin-first-core-vision.md`.

[codex-issue]: https://github.com/openai/codex/issues/27352
[codex-blog]: https://developers.openai.com/blog/run-long-horizon-tasks-with-codex
[cc-issue]: https://github.com/anthropics/claude-code/issues/56346
