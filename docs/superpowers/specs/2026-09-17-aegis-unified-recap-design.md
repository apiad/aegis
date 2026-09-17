# One recap: task, outcome, next

> **Status:** designed 2026-09-17 with Alex; not implemented. Changes the
> recap schemas of `2026-09-17-aegis-turn-attention-design.md` and the `now`
> line of `2026-09-17-aegis-fleet-dashboard-v2-design.md`. The probe behind
> every choice here is `.playground/recap-abstract-probe/` (workspace
> playground, not in this repo): `results-v1.md`, then `results.md` for the
> prompt adopted below.

## Why

Alex, after a day with the every-turn recap: it is an inventory. Measured on
the persisted `RecapNote`s of seven real sessions, it reads like
`Created 2 files (waiting-2.html, …) and committed the design spec to git
(f6d8748)` and `Queued 1 task (01M2QX…) and started 1 monitor`. One even
narrated its own prompt ("needs clarification on whether to use the facts
block…").

The cause is the prompt, not the model. The turn recap asks, twice, to
"name files and counts", and tells the model to prefer the FACTS block,
which is an inventory of commits and written files.

There are also three recaps with three schemas and three prompts: the turn
recap (`line`), the mid-turn recap (`done`/`doing`) and `/recap`
(`building`/`done`/`remaining`). They answer the same question at three
window sizes.

## The decision

**One schema, one prompt, three windows.**

| field | meaning |
|---|---|
| `task` | what the session is working toward, as a goal a person would name; never a state like "waiting" |
| `outcome` | one sentence, at most 20 words: what was just solved, decided, delivered or learned. In a turn still running, what it is doing right now |
| `next` | one short sentence: what is left or what it waits for; empty if nothing |
| `attention` | as in the turn-attention spec |

The prompt (probe v2, adopted as written):

> You tell an operator, glancing at a dashboard, where a coding agent's
> session stands. Speak at the level of intent and outcome: the problem
> being solved, what got solved or decided, what is left. Never list files,
> commits, hashes, task ids, ports, test counts, finding counts or tool
> calls, and avoid numbers unless the number is the point (a budget that ran
> out, a deadline). The FACTS block is only a guard against claiming work
> that did not happen; do not report its contents. Prefer what actually
> happened over what the agent said it would do. LANGUAGE: write every
> field in the language of the operator's own messages (the lines marked as
> the user), even when the agent answers in another language. `task` names
> the goal, never a state like waiting. `outcome` is at most 20 words. No
> preamble, no praise. A question to the operator is needs_input even when
> the turn also landed work.

Two additions to that prompt:

- the mid-turn call adds: "The turn is still running: `outcome` is what it
  is doing right now, in the present tense";
- every call passes the previous recap's `task`, when there is one, as
  "Previous task: … — keep it unless the goal changed". Measured: without
  it, one session's `task` wandered between "Design…" and "Implement…" on
  consecutive turns.

**Language is best effort.** The probe showed the model often answers in
English even when the operator writes Spanish, because queue notices fill
the window's user lines. Alex ruled this is not worth code: the prompt rule
stays and nothing detects the language.

**Windows** (`aegis.btw.window.assemble` options):

| caller | window | measured cost |
|---|---|---|
| turn recap | 3 turns, 3,000 tokens, 300 chars per item | $0.016 per call |
| mid-turn recap | 2 turns, 2,500 tokens, 240 chars (unchanged) | about $0.01 |
| `/recap` | 8 turns, 8,000 tokens, 300 chars | $0.035 |

`/recap` no longer reads up to ten turns and 32,000 tokens: Alex asked that
it not be the whole conversation.

## What each surface shows

- **Transcript recap block**: header as today (`recap · ? needs you`), body
  = `outcome`, then `task` on a muted line. The footer is unchanged.
- **`/recap`**: the same block with three labelled lines, `task`, `outcome`,
  `next`.
- **F10 list item**: the `did` line is `outcome`; the `now` line is the
  mid-turn recap's `outcome`.
- **F10 detail**: a **TASK** section before NOW, and a **NEXT** section after
  DID when it is not empty.
- **F3**: the `now …` line is the mid-turn `outcome`.
- **Persistence**: `RecapNote` carries `line` (the outcome), `attention`,
  `task` and `next`; old records decode with `task` and `next` empty.

## Out of scope

- Detecting the operator's language in code.
- Recapping unsolicited harness turns (already filed in `TASKS.md`).
