# Aegis as a technical-writing harness — vision

> **Status:** vision doc, captured from a working session on 2026-09-13. Not a
> plan, not a spec. Everything here is grounded in measurements taken that day;
> the index at the end says where each number lives. See `TASKS.md` for the
> pointer.

Aegis is a configurable meta-harness. This sketches what it looks like
configured for technical writing: a plugin that turns it into a writing
harness, with its own stages, its own checks, and eventually its own windows
and keybindings.

## Not the other writing plugin

`2026-09-12-writing-style-plugin-design.md` gates the agent's **conversation
turns** against a declared style. This one produces **artifacts**. They share
measurements and share no shape, and folding them together because they use
the same counter would be the usual mistake.

## The central idea: one mechanism, many voice packs

What makes writing personal is the corpus, not the config. Every measurement
from 2026-09-13 points the same way: exemplars beat rules, the target is the
author's own profile, and a slop list only discriminates when it is baselined
against that author.

So "customisable per user, per topic, per domain, per language" resolves to a
single mechanism plus **voice packs**. A voice is a directory:

```
voices/<name>/
  corpus/          the author's own writing in this register
  profile.json     derived: stylometric targets + n-gram overrepresentation
  exemplars/       or a selection policy over the corpus
  slot.yaml        word budget, structure rubric, optional numeric targets
```

**Language is not a field.** It falls out of the corpus: a Spanish voice holds
Spanish text and its profile derives from it. If the metrics are
language-agnostic and the data comes from the corpus, nothing English is wired
anywhere. This is the same rule the rift design landed on the same day: the
tool ships mechanism, the user ships corpus.

## The stages, and what each one learned

**Outline, with its own syntax constrained.** No colons or dashes *in the
outline*, one plain declarative sentence per idea, stating the idea rather than
labelling it. Measured: notes carrying 28 to 41 colons per 1000 words produced
prose at 12.8 to 14.5 against 6.8 for single-pass, and forbidding the
punctuation inside the outline drove it to zero. The intermediate artifact's
syntax becomes the finished text's syntax.

**Draft through `claude -p`, exemplars in the system slot, thinking off.**
`--system-prompt` replaces the agentic system prompt rather than appending to
it; `--setting-sources ""` drops CLAUDE.md, skills and plugins; `--tools ""`
removes the tool schemas. The same style text moved from the user turn into
that slot preserved word count (1153-1311 against 734-947) and recovered most
of the sentence-length variance. `MAX_THINKING_TOKENS=0` is the only real off
switch, and it cut a 1200-word generation from 137.7s to 17.5s with the same
measured output and slightly more prose.

**Measure mechanically, against the voice profile, two-sided.** This calls
rift rather than reimplementing it, once rift has
`docs/reference-corpora-design.md`. A one-sided check is actively wrong here:
Alex writes 3.68 em dashes per 1000 words, so a floor at zero scores a
zero-em-dash arm best and scores the author himself worst.

**Critique that detects and quotes, never scores.** Measured over six
documents: zero hallucinated quotes, and the critic found seven genuine
corrective constructions that a literal phrase list missed entirely, because
the construction is syntactic and its surface forms are unbounded ("The
alternative isn't", "None of this is", "Not the words —"). A model asked to
*judge* quality instead inherits self-preference bias that tracks perplexity,
which is the defect under study.

**Revise, then re-measure. The loop exits on the measurement, not on a
counter.** This is the change from today's `/draft`, which iterates N=3 without
asking whether anything improved.

## Configurable, and deliberately not

Configurable: the voice, the slot, the language, which model runs each stage,
thresholds expressed in sigma against the profile so they travel between voices.

Not configurable, because these are the findings rather than preferences:

- the critic detects and quotes, it does not score
- every quote is verified against the document before it is shown
- the measurement is mechanical
- generation stages run with thinking disabled

A plugin with no opinions is a directory of utilities.

## The future-aegis surface

Once aegis carries plugin-owned windows and keybindings, the writing harness is
where that pays off: a draft pane beside a measurement pane, the critic's
verified quotes as a navigable list, a keybinding that jumps to the next
flagged span, the voice profile visible as a target with the current draft's
position against it. None of that is needed for the pipeline to work, and all
of it is what makes the difference between running a pipeline and working in a
harness.

## What is still unknown

- Whether more than three exemplars help, and where it saturates. The winning
  arm used 236 words of the author's prose. Nobody has tried 288,000 in a 1M
  window, which is a regime that did not exist when the discouraging
  personalisation literature was written.
- Whether the iteration loop helps or harms. Version chains have been on disk
  since June (`article_drafts/draft-*-v{1..9}.md`) and have never been measured.
  If an LLM critic drives toward low perplexity, every iteration makes it worse.
- Whether any of it transfers to Spanish. There is no Alex-written Spanish
  corpus in a consistent register, so there is no instrument and no target.
- No arm reached the author's typical length: best was 1330 words against his
  average of 1960.

## Where the evidence lives

In the Workspace vault:

- `vault/Atlas/Architecture/2026-09-12-llm-prose-quality-research-plan.md`
- `…/2026-09-12-slop-profile-phase-0-findings.md` — the corpora, the audit that
  deleted a 16-word rule, the mutation tests
- `…/2026-09-12-llm-prose-quality-research-brief.md` — the six mechanisms and
  the six-rung intervention ladder, 14 sources
- `…/2026-09-13-two-pass-caveman-drafting-experiment.md` — the syntax-leak
  result
- `…/2026-09-13-style-prompt-arms-experiment.md` — eleven arms, the 39x forbid
  inversion, the thinking measurement, the system-slot result
- `vault/Efforts/Areas/Writing/voice/corpus/slop/` — every corpus, every arm,
  and `scripts/` with the generators and their README

In repos:

- `repos/rift/docs/reference-corpora-design.md` — the mechanical half
- `repos/aegis/docs/superpowers/specs/2026-09-12-writing-style-plugin-design.md`
  — the conversation-turn gate, a sibling rather than a part
- `bin/slopcheck` in the Workspace — the counter, mutation-tested on three paths

Two blind reads by the author, on different topics, both picked the passage
closest to his own measured profile rather than the cleanest one. That is the
strongest single result and it has n=2.
