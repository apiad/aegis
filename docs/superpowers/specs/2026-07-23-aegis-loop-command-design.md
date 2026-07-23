# `/loop` — a self-re-arming turn-end instruction

**Date:** 2026-07-23
**Status:** Approved — ready for implementation plan
**Owner:** Alex + Claude

## Summary

`/loop <text>` arms a **looping instruction** on a session. Every time that
session would otherwise settle idle, `<text>` is delivered back to it as a
fresh turn. The agent judges whether the instruction is satisfied; when it
is, the agent reaps the loop by calling `aegis_loop_stop`.

It is the operator-facing counterpart to `aegis_remind`: a reminder fires
once and is consumed, a loop fires repeatedly and re-arms until something
stops it.

```
/loop run the test suite and fix whatever fails until it's green
```

## Motivation

Long convergent tasks — "keep fixing until the suite passes", "keep
tightening this until the benchmark clears" — currently need the operator
to poke the agent after every turn. The agent has no way to say "I am not
done, run me again", so the human becomes the loop counter.

Claude Code has `/loop` for its own harness. aegis drives *every* harness
(claude, lovelaice, ACP), so the loop belongs in the substrate, one tier
below the agent, where it works the same for all of them.

## Non-goals

- **Agent-armed loops.** An `aegis_loop` MCP tool that lets an agent arm a
  loop on itself is deferred (see *Later*). Agents get the reap, not the arm.
- **Persistence across restart.** Loops are session-scoped and in-memory,
  like reminders. A TUI restart clears them.
- **Interval / cron loops.** No `--every <duration>` in v1. The loop fires
  at turn boundaries, not on a timer. (Deferred, see *Later*.)
- **A separate judge model.** The looping agent judges its own exit
  condition in-band. No cheap-model verifier.
- **Loops on peer sessions.** `/loop` arms the pane it's typed in. No
  `--on <handle>`.

## Design

### The fourth turn-boundary tier

`AgentSession._chain_if_pending` already runs a priority ladder at every
turn boundary. The loop becomes its lowest rung:

```
_inbox_buffer  →  unsolicited harness drain  →  _reminders  →  loop  →  idle
```

Bottom placement is what makes the loop safe to live with. Handoffs,
monitor callbacks, queue results and operator messages all land in
`_inbox_buffer` (tier 1), so **they preempt the next loop iteration** rather
than starving behind it. The loop fires only when the session has nothing
else to do and would otherwise go idle.

### `LoopState`

One loop per session (`AgentSession._loop: LoopState | None`). Arming a
second replaces the first, and the `CommandResult` says so.

```python
@dataclass
class LoopState:
    text: str                 # the instruction, re-sent verbatim each turn
    iteration: int = 0        # iterations delivered so far
    max_iterations: int = 20
```

`iteration` counts deliveries, incremented as the turn is dispatched, so the
header on the Nth delivery reads `iteration N/max`. A loop that has been
armed but never fired is at 0.

### Delivery

Each iteration is an ordinary `InboxMessage` carrying a new
`sender_loop()` tag (`aegis/queue/schema.py`, alongside `sender_reminder()`),
so it renders through the existing substrate-header machinery:

```
> from loop · iteration 3/20 · 2026-07-23T14:02:11Z
```

The body is `<text>` verbatim plus a fixed coda:

> If this instruction is now fully satisfied, call
> `aegis_loop_stop(from_handle='<handle>', reason='<why>')` and stop.
> Otherwise continue.

Re-sending `<text>` verbatim (rather than a bare "keep going") means the
loop still works when the previous turn ended somewhere unhelpful — the
instruction is present in the turn that has to act on it.

### Monitor interaction — the loop yields to `_unsolicited_hold`

`_unsolicited_hold > 0` means an aegis monitor is the authoritative waker
for this handle. The reminder tier deliberately ignores that hold; **the
loop tier must respect it.**

Without the gate, `/loop run the tests until green` composed with
`aegis_monitor` is a spin loop: the agent launches the suite, ends its turn,
the loop instantly re-fires, and the agent burns whole turns asking "done
yet?" while the monitor sits right there waiting to wake it. Gating on
`_unsolicited_hold == 0` makes the two features compose — the monitor wakes
the agent, the agent works, the turn ends, the loop re-arms.

The iteration counter does not advance on a suppressed fire.

### Termination

Five ways out, all of which clear `_loop` and report to the transcript:

1. **`aegis_loop_stop(from_handle, reason?)`** — the intended path. The MCP
   tool an agent calls when it judges the instruction satisfied.
2. **Iteration cap.** On reaching `max_iterations` the loop stops itself and
   reports `loop capped at N — the agent did not stop it`. The wording
   matters: a capped loop is not a completed one.
3. **`/loop stop`** — operator reap.
4. **Interrupt (Esc).** `AgentSession.interrupt()` clears the loop. Without
   this there is no escape: the loop re-fires the instant the interrupted
   turn ends, and Esc becomes useless. Interrupt means stop.
5. **Harness error.** `last_error` set during a loop iteration stops the
   loop. Otherwise a broken session spins on its own error forever.

### Command surface

`aegis/commands/` is harness-agnostic by construction (no Textual import;
the web client reuses `dispatch` verbatim), so one registration in a new
`aegis/commands/builtins/loop.py` lands `/loop` in the TUI and the PWA for
every driver.

| form | effect |
|---|---|
| `/loop <text>` | arm on the current pane (replaces any existing loop) |
| `/loop --max N <text>` | arm with a non-default cap |
| `/loop` | show status: text, iteration, cap |
| `/loop stop` | reap |

`ArgSpec` is a single greedy positional (`Arg("text", required=False,
greedy=True)`) plus `Flag("max")`. A trailing greedy positional takes the raw
un-tokenized remainder and stops flag parsing, so `--max 5` binds before the
text and any `--x` *inside* the instruction survives verbatim.

That greedy positional makes `stop` ambiguous with an instruction that
happens to start with the word. The rule: the argument string is a verb only
when it is **exactly** `stop` (or empty, for status). `/loop stop the dev
server and restart it` arms a loop, as typed. The one thing you cannot
express is a loop whose entire instruction is the single word `stop`, which
is not an instruction.

Bridge surface, mirroring how `aegis_remind` reaches `AgentSession`: a
`loop_service` attribute on `AppBridge` holding a `LoopService` with
`arm(from_handle, text, max_iterations)`, `stop(from_handle, reason)` and
`status(from_handle)` — constructed on both `AegisApp` and the headless
`SessionManager` so the TUI and `aegis serve` behave alike, and stubbed in
remote mode like the other local-only planes.

The service is a handle→session shim over `AgentSession.arm_loop` /
`stop_loop` / `loop_status`; it exists so the MCP plane and the command
plane share one lookup rather than each reaching into the session map.

### Display

The StatusBar gains a loop segment next to the build string:

```
aegis 0.21.0+d35b07a  opus  high    ⟳ loop 3/20    working
```

Absent when no loop is armed.

## Error handling

- **Arming with empty text** → `CommandResult(ok=False)`, nothing armed.
- **`/loop stop` with no loop armed** → `ok=False`, "no loop armed".
- **`aegis_loop_stop` from a handle with no loop** → `{"error": ...}`, not an
  exception. An agent calling it twice is harmless.
- **`--max` non-integer or `< 1`** → rejected by `LoopService.arm`, not by the
  parser. `Flag` carries no type, so `ArgSpec` hands back the raw string;
  validating in the service means the MCP plane gets the same check for free.
- **Session closes mid-loop** → `_loop` dies with the session; no cleanup
  path needed (in-memory, session-scoped).

## Testing

Unit, against `AgentSession` directly:

- A loop re-fires at turn end and increments the iteration counter.
- Tier order: with a buffered inbox message *and* an armed loop, the inbox
  message dispatches first and the loop fires on the following boundary.
- Tier order vs reminders: a pending reminder goes before the loop.
- `_unsolicited_hold > 0` suppresses the fire and does **not** advance the
  counter; releasing the hold lets it fire.
- The cap stops the loop and reports "capped", distinct from a clean reap.
- `interrupt()` clears an armed loop.
- A harness error during an iteration stops the loop.
- `aegis_loop_stop` on an unarmed handle returns an error dict.

Command-level, against `dispatch()`:

- `/loop <text>`, `/loop --max N <text>`, `/loop`, `/loop stop` each produce
  the right `CommandResult` and bridge call.
- `/loop stop the dev server` arms rather than reaping — the verb rule is
  exact-match only.
- `--max` inside the instruction text survives verbatim (greedy positional
  stops flag parsing).
- Arming twice replaces and says so.

MCP registration test for `aegis_loop_stop`, matching the pattern in
`tests/test_reminder.py`.

## Later

- **Agent-armed loops (`aegis_loop`).** Deferred. When built, it is
  **gated by human approval the same way dynamic workflows are**: an
  agent-invoked arm returns `{status: "gated", ...}` for the operator to
  approve, while operator-invoked arming proceeds directly — the
  `gate_decision(projected_agents, threshold, operator_invoked)` shape in
  `aegis/dsl/gate.py`. An agent that can arm its own unbounded loop is a
  runaway generator; the operator stays in the approval path.
- **Budget-backed caps.** `aegis/budget` already expresses
  `constraint: usd | output_tokens` over a window and enforces it at queue
  enqueue. A loop budget would be the cost-aware version of the iteration
  cap. Iterations first; revisit if the cap proves to be the wrong unit.
- **`--every <duration>`.** Turns the loop into a poller rather than a
  tight turn-boundary loop.
- **Persistence across restart.** Rejected for v1 — auto-firing a restored
  loop means a cold TUI starts spending tokens at boot without anyone
  asking for it.
