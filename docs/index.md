---
title: Aegis
hide:
  - navigation
  - toc
---

<div class="aegis-hero" markdown>

# aegis { .aegis-hero-mark }

<p class="aegis-hero-tagline" markdown>
The **meta-harness**. Drive Claude Code, Gemini CLI, and OpenCode side by side from one calm terminal — and make them *collaborate*.
</p>

<div class="aegis-hero-cta" markdown>
`pip install aegis-harness`
[Quickstart →](install.md){ .aegis-button }
</div>

</div>

<div class="aegis-term hero">
<div class="aegis-term-chrome">
<span class="aegis-term-dot accent"></span>
<span class="aegis-term-dot"></span>
<span class="aegis-term-dot"></span>
<span class="aegis-term-title">aegis · 3 agents · ~/code/aegis</span>
</div>
<div class="aegis-term-body"><span class="t-accent">●</span> 1 lucid-knuth <span class="t-muted">·opus·</span>   <span class="t-success">●</span> 2 wry-hopper <span class="t-muted">·gemini·</span>   <span class="t-accent">●</span> 3 brisk-curie <span class="t-muted">·opencode·</span> <span class="t-accent">*</span>

<span class="t-user">› explain the retry path in worker.py and have wry-hopper draft a unit test for it</span>

<span class="t-muted">⠹ Thinking… (3.2s)</span>
<span class="t-accent">⏺</span> Read(worker.py)
   └ ok
The retry lives in <span class="t-bold">_run_turn</span> at line 142. On harness-error it
captures the exception, commits zero-token metrics, then chains a
follow-up turn from the buffered inbox.

<span class="t-accent">⏺</span> aegis_handoff(target=wry-hopper)
   └ delivered to wry-hopper

<span class="t-muted">queues: tests ●1/2 ○0 ✓3 ✗0    last: brisk-curie</span>
lucid-knuth <span class="t-muted">·opus· opus·full</span>   ↑128k (94% cached) ↓1k
<span class="t-muted">─────────────────────────────────────────────────────────────────────────</span>
› ask something…</div>
</div>

<section class="aegis-section" markdown>

## Above the harness, not beside it { .aegis-section-h }

<p class="aegis-lead" markdown>
Most agentic frameworks talk **directly to LLM providers** — they replace your coding agent. Aegis takes the opposite path: it sits **above** the coding agents you already use, drives them over their structured protocols, and adds a routing + delegation plane on top.
</p>

<div class="aegis-stack" markdown>

<div class="col dim" markdown>
#### Most agent frameworks
Talk to LLM providers (OpenAI, Anthropic). Replace your coding agent. Reimplement tool use, permissions, sandboxing. Every provider needs new glue.
</div>

<div class="col" markdown>
#### Aegis
Talks to **agents** — Claude Code (stream-json), Gemini CLI (ACP), OpenCode (ACP). Doesn't replace them; orchestrates them. New providers slot in behind one driver seam.
</div>

</div>

<p class="aegis-lead muted" markdown>
The harness wars are over. You probably already have your favorite (or two, or three). Aegis lets you keep them — and make them work as a team.
</p>

</section>

<section class="aegis-section" markdown>

## Five primitives for agent coordination { .aegis-section-h }

<p class="aegis-lead" markdown>
Multi-agent systems need more than a chat box. Aegis ships **five composable coordination primitives**, each with one verb, each delivered through the same calm `✉` block in the receiving agent's transcript.
</p>

<div class="aegis-primitives" markdown>

<div class="aegis-card" markdown>
<div class="aegis-card-glyph">→</div>
### Inbox · *send context to a peer*
Any agent can hand off context to any other live agent. Fire-and-forget. The recipient gets a normal user-message turn, tagged with the sender's handle.

<div class="aegis-term">
<div class="aegis-term-body"><span class="t-accent">⏺</span> aegis_handoff(target=wry-hopper, context="…")
   └ delivered to wry-hopper

<span class="t-muted">--- in wry-hopper's pane ---</span>
<span class="t-accent">✉</span> from agent:lucid-knuth · 17:42:03Z
  Please review the retry path in worker.py.</div>
</div>
</div>

<div class="aegis-card" markdown>
<div class="aegis-card-glyph">⏳</div>
### Queue · *spawn a worker on demand*
Enqueue a task to a named queue; the substrate spawns a fresh agent of the configured profile, runs the payload, and delivers the result back to your inbox. Producer keeps working in between.

<div class="aegis-term">
<div class="aegis-term-body"><span class="t-accent">⏺</span> aegis_enqueue(queue="review", payload="…")
   └ {task_id: 01HK…, queued_position: 1}

<span class="t-muted">--- 4 minutes later ---</span>
<span class="t-accent">✉</span> from queue:review · task#01HK… · <span class="t-success">ok</span>
  PR looks clean. Two nits flagged.</div>
</div>
</div>

<div class="aegis-card" markdown>
<div class="aegis-card-glyph">▦</div>
### Canvas · *collaborate on a document*
Open a shared markdown file. Multiple agents read, write sections, subscribe. Each write wakes every other subscriber with a diff-aware notification. The classical blackboard pattern, terminal-native.

<div class="aegis-term">
<div class="aegis-term-body"><span class="t-accent">⏺</span> aegis_canvas_write_section(name="report-q3", section="data", …)

<span class="t-muted">--- in pm's pane ---</span>
<span class="t-accent">✉</span> from canvas:report-q3 · 20:30:00Z
  section "data" · written by agent:researcher (+18 / -3 lines)
  ──
  Q3 numbers came in stronger than projected…</div>
</div>
</div>

<div class="aegis-card" markdown>
<div class="aegis-card-glyph">▮</div>
### Terminal · *share a live shell*
Spawn a PTY-backed shell any agent (or you) can run commands on, send raw keystrokes to, and subscribe to. Command boundaries come from OSC 133 markers; every finalized command wakes subscribers with the cmd, exit code, and a tail of output.

<div class="aegis-term">
<div class="aegis-term-body"><span class="t-accent">⏺</span> aegis_term_run(name="build", cmd="pytest -q")
   └ exit 0 · 4.20s

<span class="t-muted">--- in pm's pane ---</span>
<span class="t-accent">✉</span> from term:build · 14:03:25Z
  $ pytest -q  · run by agent:builder
  exit 0 · 4.20s
  ──
  6 passed in 4.18s</div>
</div>
</div>

<div class="aegis-card" markdown>
<div class="aegis-card-glyph">⟳</div>
### Workflow · *deterministic Python orchestration*
When the dance has to be reliable — TDD loops, bug triage, multi-step plans — wrap it in a workflow. Plain Python, calls agents, runs bash predicates, retries with feedback. Top of the stack; spans agents.

<div class="aegis-term">
<div class="aegis-term-body"><span class="t-accent">⏺</span> aegis_run_workflow(name="tdd-cycle", kwargs={"feature": "…"})

<span class="t-muted">workflow:tdd-cycle ▶ implementer (writing failing test)</span>
<span class="t-muted">workflow:tdd-cycle ▶ predicate: pytest tests/test_x.py::… (FAIL ✓)</span>
<span class="t-muted">workflow:tdd-cycle ▶ implementer (implementing)</span>
<span class="t-muted">workflow:tdd-cycle ▶ predicate: pytest tests/test_x.py (PASS ✓)</span>
<span class="t-muted">workflow:tdd-cycle ▶ reviewer (final pass)</span></div>
</div>
</div>

</div>

</section>

<section class="aegis-section" markdown>

## What's also in the box { .aegis-section-h }

<p class="aegis-lead" markdown>
The TUI is calm and dense. The metrics are honest. The substrate persists.
</p>

- **Multi-tab TUI.** N independent agent sessions. Generated alliterating handles (`lucid-knuth`, `wry-hopper`). State dots, sticky `*`, terminal bell when a backgrounded agent finishes. Per-block click-to-copy.
- **Honest metrics.** True input (including cache) with cached %, output, tool calls, per-turn and per-session timing. Provisional while streaming, exact at turn end. No log scraping anywhere.
- **Queue dashboard.** Always-on one-line strip above the status bar (per-queue depth, last in-flight worker). `Ctrl+D` for a full dashboard with in-flight / queued / recent bands and a live assistant-text tail.
- **Session persistence.** `aegis` reopens the last workspace by default — tabs, profiles, order, with each underlying agent session genuinely resumed. `aegis --clean` opts out.
- **Headless + Telegram.** `aegis serve` runs the SessionManager + MCP plane without a TUI. Add a Telegram token and drive your agent team from your phone.
- **MCP plane.** Every spawned agent is injected with the aegis MCP server. Orientation (`aegis_meta`), session listing, handoff, queue dispatch, canvas ops, workflow invocation — one consistent surface across providers.

</section>

<div class="aegis-final" markdown>

## Get aegis

```bash
pip install aegis-harness   # Python 3.13+
aegis init                  # interactive wizard
aegis                       # full-screen TUI
```

[Install →](install.md){ .aegis-button }
[Usage →](usage.md){ .aegis-button }
[Roadmap →](roadmap.md){ .aegis-button }

</div>
