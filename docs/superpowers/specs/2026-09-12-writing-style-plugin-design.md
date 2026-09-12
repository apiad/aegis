# Writing style: a measured gate on the conversation turn

> **Status:** design, 2026-09-12. Not yet planned. Brainstormed against the
> measurements in `vault/Atlas/Architecture/2026-09-12-slop-profile-phase-0-findings.md`
> (the Workspace vault), which supplies every number quoted here.

A plugin that holds one side of a conversation to a declared writing style:
detailed guidance at session start, a short adaptive reminder each turn, and a
measurement at the turn boundary that can spend one repair turn when the text
breaks a rule that admits no judgement.

## Scope

**The conversation turn only.** What the agent says to the operator through
aegis. Files the agent writes are out of scope and stay with repo tooling
(`rift`) and skills, which already run where the file lands and can see the
whole artifact rather than one turn of it.

That decision removes the `PreToolUse` / `updatedInput` branch from the design
entirely, and with it the need for a per-target scope field on a style.

## The third sibling at the turn boundary

Two features already fire at the turn boundary and this is the third:
`2026-08-26-aegis-turn-boundary-generation-design.md` shipped the **loop judge**
and the **recap**, both reading the same `TurnFacts`, both best-effort by
contract. The writing-style gate reads the same boundary and reuses the same
posture, with one difference that matters: **it spends no tokens to decide.**
The judge and the recap each cost a structured-generation call. The gate counts
strings.

The loop judge also settles the authority question. Its spec says the agent
deciding whether the agent is done is the agent grading its own homework. The
same holds for prose, so the verdict is the plugin's and never the agent's.

## What aegis already provides

`AgentSession._chain_if_pending()` (`src/aegis/core/session.py:929`) is a
four-tier ladder run at every turn boundary:

1. `_inbox_buffer`: peer and queue messages.
2. `has_pending_event()`: unsolicited harness events.
3. `_reminders`: notes the session left for itself, via `add_reminder()`.
4. `_loop`: the operator's looping instruction, gated by the judge.

Tier 3 is the repair turn, already built. `add_reminder()`
(`src/aegis/core/session.py:494`) delivers a note as the session's own last
turn, strictly behind inbox and unsolicited drain, and **promotes the chain
itself when the session has already gone idle**. So a hook that finishes
measuring after the boundary has passed still gets its turn.

## Two changes to aegis core

Both are small and both mirror a shape that already exists. Both also move one
step toward `2026-08-23-aegis-plugin-first-core-vision.md`, which records that
the plugin surface today "can add behavior but cannot replace or extend the
substrate".

### 1. `PostTurnResult`

`post_turn` hooks are observers: `run_observer_hooks` is launched with
`asyncio.create_task` and the payload carries a read-only `SessionHandle`, so a
hook cannot reach `add_reminder`. Add a result type mirroring `PreTurnResult`:

```python
@dataclass(frozen=True)
class PostTurnResult:
    """Optional return from a post_turn hook."""
    remind: str | None = None   # delivered as a tier-3 self-reminder
```

The runner collects results and calls `add_reminder()` for each non-empty
`remind`. Hooks that return `None` keep today's observer behaviour, so every
existing hook is unaffected.

### 2. `PostTurnEvent.own_message`

`PostTurnEvent.assistant_message` is `"".join(assistant_text_parts)`, which is
**deliberately inclusive of subagent narration**. The session already computes
the other thing next to it: `own_text_parts`, filtered on
`parent_tool_use_id is None`, with the comment at `session.py:681` saying "A
subagent's narration is not this turn's answer."

The gate needs `own_text_parts`. Subagent narration is not what the operator
reads as the answer, and the agent cannot repair text a subagent wrote. Add
`own_message: str` to the payload and leave `assistant_message` alone.

## The style artifact

A directory under the config root, resolved like `plugin_dirs`:

```
.aegis/styles/<name>/
  style.yaml
  profile.json      # optional: a slopcheck profile
```

`style.yaml` carries one list of checks plus the guidance prose:

```yaml
name: apiad-conversational
min_words: 120          # shorter turns skip the gate entirely
max_repairs: 1

checks:
  - id: metaphor-nouns
    kind: forbid
    phrases: [substrate, scaffolding, primitive, bedrock, modality, ratchet]
    budget: 0
  - id: metaphor-noun-vector
    kind: forbid
    phrases: [vector]
    lang: en            # the one measured collision: Spanish "vector" is a real word
    budget: 0
  - id: corrective
    kind: forbid
    phrases: ["it isn't", "it's also", "that's the", "that's not", "this isn't"]
    budget: 1
  - id: em-dash
    kind: budget
    pattern: "—"
    per_1000: {en: 5.0, es: 28.0}
  - id: emoji
    kind: budget
    pattern: "@emoji"    # a leading @ names a built-in matcher; anything
                         # else is a literal string
    per_1000: {any: 0.0}
  - id: sentence-length
    kind: measure       # reported to the operator, never shown to the agent
    target: {en: 17.8, es: 10.8}

guidance: |
  The full text injected at session start.
```

`unslop` becomes one instance of this shape: roughly thirty `forbid` checks,
four `budget` checks, and `slop-en-control.json` as its profile. The skill is a
writing style that happens to be written in prose.

## One source, three renderings

The three texts are generated from the check list, never written separately.
Written separately they drift, and in six months the reminder names one rule
while the gate measures another.

- **Session start.** `guidance` plus the `forbid` list with its measured rates.
  Delivered as `prepend_system` on the first `pre_turn` (works in every driver)
  or as `--append-system-prompt` via `pre_spawn` (survives compaction, claude
  driver only). Start with the first; the second is a later option.
- **Pre-turn.** The three checks that have fired most often *in this session*,
  named, nothing else. Adaptive, so the reminder stops spending tokens on rules
  the agent is not breaking.
- **Repair.** Generated from the turn's actual hits. **The measurement, never
  the rule.** "substrate 4 times and 9 em dashes in 380 words, against a budget
  of 2" leaves nothing to interpret; "avoid em dashes" depends on the agent
  agreeing with the rule.

## Language

Measured over 130,779 tokens of Alex's Spanish against 288,150 of his English.
Three classes of check fall out, and only one of them needs language detection.

### Literal phrases are self-scoping

Of the 72 phrases the current rule set names, **68 cannot appear in Spanish
text at all**. A literal-phrase check therefore needs no detection: "delve"
cannot match Spanish because Spanish does not contain "delve".

The four that do appear:

| term | hits | per million | what it is |
|---|---|---|---|
| vector | 19 | 145.3 | real Spanish: "el mosquito como vector" |
| features | 7 | 53.5 | English embedded in mixed documents |
| delve | 1 | 7.6 | a page quoting the rule that bans delve |
| leverage | 1 | 7.6 | English embedded in mixed documents |

So exactly one term needs a `lang:` tag today. The tag is the exception, not
the default.

### Punctuation budgets are the most language-dependent thing here

| measure | Alex, English | Alex, Spanish | ratio |
|---|---|---|---|
| em dash per 1000 words | 4.02 | 28.19 | 7.0x |
| mean sentence length | 17.8 | 10.8 | 0.6x |

Spanish uses the raya for dialogue and for parentheticals as ordinary
typography. A single global em-dash budget is wrong by a factor of seven.

### Only formatting of the medium is language-independent

Emoji, bold spray, title-case headings, heading density. These are markdown
conventions rather than conventions of a language, and they carry one budget
for every language.

### Detection sits off the critical path

Because detection is needed only for budgets, a detection error cannot produce
a false gate as long as the fallback is right.

- **Unit: the paragraph.** Turns mix a Spanish body with English quotations. A
  per-turn verdict is wrong in both directions; per-sentence has too little
  signal.
- **Method: function-word counting**, already in `slopcheck.detect_lang`. No
  new dependency. It correctly kept 72 Spanish Wiki pages and 198 Spanish
  Sources out of the English corpora during phase 0.
- **Prior: the operator's own message.** `PostTurnEvent.user_message` is
  already in the payload, and the agent answers in the language it was
  addressed in. That is a stronger signal than any count over the agent's own
  text, and no general-purpose detector has access to it.
- **Fallback: when detection is uncertain, the check reports and does not
  gate.** An ambiguous paragraph costs a slightly wrong number in a report,
  never a repair turn the agent did not earn.

## Use versus mention

One of the four collisions above is not a language collision at all:

> El critic dice "viola P3 (no uses 'delve')" no "esto suena mal".

A page that *discusses* a rule *trips* the rule. This is the failure that would
have broken the plugin on its first day: the brainstorming conversation that
produced this document named substrate, delve and "it isn't" dozens of times as
specimens, every turn would have gated, and the repair turn would gate too,
because explaining the failure means naming the words again.

The fix is text hygiene and is mostly already written.
`slopcheck.strip_markdown` removes fenced code, inline code and tables. It must
also remove **block-quote bodies**, which it currently keeps.

Then it becomes a convention: **a word named as a specimen goes in backticks.**
That converts an ambiguity the checker cannot resolve into a mark the author
makes on purpose.

## Gate discipline

**Only `forbid` and `budget` checks can gate. `measure` checks never do.**

A `forbid` is binary and specific: the word is there or it is not, and there is
nothing to optimise except not writing it. A `measure` is a gradient, and a
gradient plus an automatic repair turn gives the agent a mechanical incentive
to drive the number down. It drives em-dash density down by writing short flat
sentences, which improves the metric and degrades the prose. Measures are
written to a file for the operator and never shown to the agent.

A `budget` sits between the two and gates only because its threshold is coarse:
a count per turn, not a score.

**Budgets, strict but sensible, from day one.** Each derives from Alex's own
rate over a 400-word turn, so the agent is held to his measured practice rather
than to zero:

| check | English | Spanish |
|---|---|---|
| metaphor nouns | 0 | 0 |
| contracted corrective | 1 | no rule yet |
| em dash | 2 per 400 words (5.0/1000) | 11 per 400 words (28/1000) |
| mid-sentence colon | 1 | not yet measured |
| decorative emoji | 0 | 0 |
| title-case heading | 0 | 0 |

**Repair cap: one.** If the repair turn also breaks a rule, the plugin reports
and gives up. Without the cap the repair measures itself and the loop has no
floor.

**Turn floor: `min_words`.** A turn under the floor skips the gate. Spending a
model turn to fix one em dash in "done, pushed" is absurd.

## Cost

The gate itself is string counting over one turn's text: microseconds, zero
tokens. The cost appears only when it fires, as one extra turn of the same
agent at the same model.

Which means the honest cost question is how often it fires, and that is exactly
what the budgets control. The sibling spec measured cost before implementing;
this one should measure **fire rate** before turning the gate on, by running
the checks in report-only mode over a week of real transcripts in
`.aegis/state/sessions/*.jsonl` and counting how many turns would have gated.

## How this could be worthless

**It fires constantly and teaches everyone to ignore it.** The `rift` lesson:
a mostly-red rule trains people to skim past red. Report-only mode first, and
promote to gating only when the fire rate is one the operator would actually
want interrupted.

**It fires on the wrong thing and the agent cannot tell.** Use versus mention
is the known instance. There will be others, and the signal is a repair turn
where the agent's honest answer is "that hit is correct text". The plugin
should make that cheap to report rather than arguing.

**The Spanish budget is calibrated on the wrong genre.** 28.19 em dashes per
1000 words comes from the encyclopedia, a children's book full of dialogue,
where the raya marks speech. Conversational Spanish almost certainly runs
lower. The number is a placeholder until a conversational Spanish corpus
exists, and it errs loose, which is the safe direction.

**The check cannot fail.** Every check ships with a mutation test: inject the
pattern into a clean sample, confirm the count rises, confirm an untouched copy
scores identically. `bin/slopcheck` in the Workspace already does this for its
three paths and the plugin inherits the obligation.

## Deferred

- Binding a style to a specific agent in `.aegis.yaml`. One style per project
  to start.
- Spanish phrase rules. The corpus does not exist yet; Spanish gets budgets and
  formatting checks only.
- Any UI for the `measure` reports. They start as a file.
- An LLM in the loop. `drivers/oneshot.py` plus `supports_oneshot` is the seam
  if a generated critique or rewrite is ever wanted, and `core/loop_judge.py`
  is the worked example. It stays deferred because a model judging prose
  carries self-preference bias that tracks perplexity, which is the defect
  under study; a counter does not.
- `--append-system-prompt` delivery of the guidance.

## Open items

- Where a style resolves when the session has no project `.aegis/`. Follow
  `AegisRoots.config_root` and fail quietly with no style rather than
  inventing a user-level config, which aegis does not have today.
- The mid-sentence colon budget for Spanish is unmeasured.
- Whether the pre-turn reminder should ever be empty. A session with no
  failures arguably deserves no reminder at all.
