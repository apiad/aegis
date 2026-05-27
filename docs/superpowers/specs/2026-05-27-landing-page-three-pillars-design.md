---
title: Landing-page three-pillar reframing
date: 2026-05-27
status: draft
---

# Landing-page three-pillar reframing

## Motivation

Aegis's landing surfaces today (README + mkdocs `docs/index.md`) lead
with a one-pillar framing — "the meta-harness." That captured the
project when meta-harnessing was the only story. Two things shifted:

- **Multi-agent** isn't implicit anymore — six coordination
  primitives are first-class shipped surfaces (inbox, queue, canvas,
  terminal, groups, workflow).
- **Programmable** has matured into a real story — `@workflow`
  scaffolding, scheduled jobs, and (as of v0.13.0) agents that can
  extend the substrate from inside via MCP config-edit.

The landing page should lead with the three-pillar phrase:

> **The programmable multi-agent meta-harness.**

— and structure the top of the page so a new visitor decodes the
project in under thirty seconds.

## Scope

In scope:

- The hero block of `README.md` (lines 1–55).
- The hero block of `docs/index.md` (the `aegis-hero` div).
- The "Above the harness, not beside it" section (README ~33–55,
  with an analog in `docs/index.md`).
- Lightweight one-line "pillar tags" on the existing primitives /
  workflows / schedules sections so the framing is legible without
  rearranging them.

Out of scope (intentionally not touched):

- Section order below the new hero+pillars block.
- The six-primitives section itself (already serves the multi-agent
  pillar cleanly).
- "What else is in the box", "Remote plane", "Per-queue budgets",
  "Scheduled workflows", "Install", "Quickstart", "Keys",
  "Configuration", "Headless + Telegram", "Docs", "Status",
  "License" — all kept verbatim.
- The mkdocs nav / sidebar.

## Hero (option A from brainstorm)

Both README and `docs/index.md` lead with the bold tagline + one
tri-pillar elevator sentence:

```
# aegis

**The programmable multi-agent meta-harness.**

Drives Claude Code, Gemini CLI, and OpenCode in one terminal, gives
them six primitives for working together, and lets you orchestrate
them with deterministic Python workflows and scheduled jobs.
```

In `docs/index.md` the same words land inside the existing
`aegis-hero` styled block (brand mark, tagline class, subtitle class,
CTA row) — no CSS changes; just the words flowing into the existing
template.

The ASCII terminal mockup (README) and the `aegis-term hero` styled
mockup (`docs/index.md`) sit immediately below, unchanged.

## "What aegis is" — the three-pillar prose section

The existing `## Above the harness, not beside it` section gets
replaced (not amended) by a new section: `## What aegis is`. Three
short paragraphs, one per pillar, in this order — meta-harness first
(it's the deepest claim), multi-agent second, programmable third
(the newest layer). Each paragraph leads with a bolded pillar name.

```
## What aegis is

**Meta-harness.** Most agentic frameworks (CrewAI, LangGraph,
AutoGen, the long list) talk directly to LLM providers — they
replace your coding agent and reimplement tool use, permissions,
sandboxing, terminal integration. Aegis sits *above* your existing
coding agents and drives them over their structured protocols —
`stream-json` for Claude Code, the Agent Client Protocol (ACP) for
Gemini CLI and OpenCode, with a clean driver seam for whatever
lands next. The harness keeps owning tool use, model selection, MCP
hosting, sandboxing. Aegis owns the layer above — tabs, routing,
delegation, persistence — the things a single-conversation CLI was
never built to do.

**Multi-agent.** Six composable coordination primitives, all wired
into one MCP plane every spawned agent sees. **Inbox** for
fire-and-forget context handoff. **Queue** for spawn-a-worker-on-
demand dispatch. **Canvas** for shared markdown blackboards.
**Terminal** for live shared PTYs. **Groups** for broadcast-and-
gather across a committee. **Workflow** for deterministic Python
orchestration. Mix providers freely — a Claude tab hands off to a
Gemini tab; an OpenCode worker drops its result in your inbox; three
agents co-author a canvas; a workflow drives all of them in
lockstep.

**Programmable.** The substrate is scriptable from the outside *and*
the inside. Outside: `@workflow`-decorated Python functions that
compose the six primitives into deterministic procedures (TDD loops,
branch reviews, spec-to-plan-to-implementation pipelines), cron-
scheduled or invoked on demand. Inside: spawned agents can mutate
`.aegis.yaml` themselves through MCP — declare a new specialised
agent profile, register a queue, drop a plugin dir, and dispatch
work to it in the same session, no restart. The substrate extends
from inside.
```

Decision points captured:

- The old closing line ("The harness wars are over. You probably
  already have your favorite…") is dropped. It was a one-pillar
  punchline; the new three-pillar framing doesn't need it.
- The "meta-harness" paragraph runs longer than the other two
  because the framing-against-CrewAI/LangGraph contrast is the
  decisive claim for a first-time visitor. Acceptable.
- Bolded inline primitive names in the multi-agent paragraph mirror
  the way the six-primitives section names them downstream.

## Existing-section pillar tags

Each of the following existing sections gets a single italic line
under its H2 heading, one line, no styling beyond standard markdown
emphasis. These tags make the pillars legible from a section scan
without rearranging content:

- `## Six primitives for agent coordination`
  → `*— the multi-agent pillar.*`
- `## Per-queue budgets` and `## Scheduled workflows`
  → both get `*— the programmable pillar.*`
- The new `## What aegis is` section gets no tag (it *is* the pillar
  summary).
- "Above the harness" remnant is gone, replaced.

Other sections ("What else is in the box", "Remote plane", "Install",
etc.) get no tag — they are infrastructure / how-to and don't need a
pillar label.

## Files touched

- `README.md` — lines 1–55 rewritten (hero + new section). Pillar
  tags inserted under three downstream H2s. Everything else
  unchanged.
- `docs/index.md` — `aegis-hero` div's text updated. Existing
  `aegis-hero-tagline` class wraps the bold tagline + new elevator
  sentence. Sections downstream (under `aegis-section` divs) get the
  pillar-tag italic lines in the same way.

No CSS changes. No new files. No section reordering. No nav changes.

## Validation

- Open `README.md` raw on GitHub; first screen on a 1440×900 monitor
  should show the bold tagline + elevator sentence + at least the
  top half of the ASCII mockup.
- `mkdocs serve` locally; `docs/index.md` first viewport on the same
  monitor shows the styled tagline + the start of the terminal
  mockup; bottom-of-page nav remains the same.
- `grep -n "harness wars are over" README.md docs/index.md` returns
  no hits (the dropped line is fully removed).
- `grep -n "the multi-agent pillar\|the programmable pillar" README.md`
  returns three lines (one under primitives, one under budgets, one
  under schedules).

## Out of scope (deferred)

- Section reordering to mirror the pillars (option C in brainstorm).
  Considered, rejected — too much movement for too little gain;
  deep-links and the mkdocs nav would need to shift in lockstep.
- Adding pillar icons / glyphs to the tag lines. Plain italic is
  enough; glyphs would compete with the section H2s.
- A "what's new in v0.13.0" callout. The CHANGELOG covers it; the
  landing page shouldn't carry per-release messaging.
