# Aegis — Tasks / Next

Working roadmap for what's next. Curated public roadmap lives in
`docs/roadmap.md`; this file is the scratch/priority list.

## Next up (captured 2026-05-18)

### 1. Delegation — needs brainstorm (define what it means)

Open design question, not yet specced.

- Premise: reuse the current substrate as-is. Delegation could just mean:
  spawn an agent, give it the task, and **substrate-level** logic makes
  the *delegator* await until the spawned agent(s) finish — not the
  Claude Code session itself blocking, but the substrate doing the
  awaiting, so it can wait indefinitely until all delegated agents are
  done.
- Open: are delegated agents **ephemeral** (spawn → run → close down on
  completion) or do they **stay open** after finishing?
- Hypotheses to explore:
  - delegation = `spawn` + `handoff` (compose the two slice-2 primitives);
  - or delegation is its own primitive where the spawned agent's result
    **automatically returns to the delegator** (leaning toward this —
    "maybe we should do that").
- → brainstorm this first; it gates the workflow scaffold.

### 2. Workflow scaffold

A harness-level scaffold for the "workflow idea": something runnable at
the harness level that spawns agents, gives them tasks, and directs one
to hand off to another — a way to **drive the whole harness
deterministically from the top**. (Cf. the sidelined `legacy/`
workflow-engine prototype.) Depends on delegation being defined.

### 3. Telegram interface — **done (2026-05-19)**

Shipped via `aegis serve` + `src/aegis/telegram/`. See README "Headless /
Telegram" and CHANGELOG `[Unreleased]`.

### 4. Task queue — **prioritized**

A task queue.

**Vision priority:** of the above, the Telegram interface and the task
queue are the two prioritized for the vision. Delegation needs a
brainstorm before it can move; the workflow scaffold sits on top of it.
