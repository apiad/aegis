---
title: Aegis
hide:
  - navigation
  - toc
---

<div class="aegis-hero" markdown>

# aegis { .aegis-hero-mark }

<p class="aegis-hero-tagline" markdown>
**The programmable multi-agent meta-harness.**<br><br>
Drives Claude Code, Gemini CLI, and OpenCode in one terminal, gives them six primitives for working together, and lets you orchestrate them with deterministic Python workflows and scheduled jobs.
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

## What aegis is { .aegis-section-h }

**Meta-harness.** Most agentic frameworks (CrewAI, LangGraph,
AutoGen, the long list) talk directly to LLM providers — they replace
your coding agent and reimplement tool use, permissions, sandboxing,
terminal integration. Aegis sits *above* your existing coding agents
and drives them over their structured protocols — `stream-json` for
Claude Code, the Agent Client Protocol (ACP) for Gemini CLI and
OpenCode, with a clean driver seam for whatever lands next. The
harness keeps owning tool use, model selection, MCP hosting,
sandboxing. Aegis owns the layer above — tabs, routing, delegation,
persistence — the things a single-conversation CLI was never built to
do.

**Multi-agent.** Six composable coordination primitives, all wired
into one MCP plane every spawned agent sees. **Inbox** for
fire-and-forget context handoff. **Queue** for spawn-a-worker-on-
demand dispatch. **Canvas** for shared markdown blackboards.
**Terminal** for live shared PTYs. **Groups** for broadcast-and-
gather across a committee. **Workflow** for deterministic Python
orchestration. Mix providers freely — a Claude tab hands off to a
Gemini tab; an OpenCode worker drops its result in your inbox; three
agents co-author a canvas; a workflow drives all of them in lockstep.

**Programmable.** The substrate is scriptable from the outside *and*
the inside. Outside: `@workflow`-decorated Python functions that
compose the six primitives into deterministic procedures (TDD loops,
branch reviews, spec-to-plan-to-implementation pipelines), cron-
scheduled or invoked on demand. Inside: spawned agents can mutate
`.aegis.yaml` themselves through MCP — declare a new specialised
agent profile, register a queue, drop a plugin dir, and dispatch work
to it in the same session, no restart. The substrate extends from
inside.

</section>

<section class="aegis-section" markdown>

## Six primitives for agent coordination { .aegis-section-h }

<p class="aegis-lead" markdown>
*— the multi-agent pillar.*
</p>

<p class="aegis-lead" markdown>
Multi-agent systems need more than a chat box. Aegis ships **six composable coordination primitives**, each with one verb, each delivered through the same calm `✉` block in the receiving agent's transcript.
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
<div class="aegis-card-glyph">▣</div>
### Groups · *broadcast and gather*
Form a named committee of agents that share one inbox-fanout channel. Send one structured four-field question; collect N parallel replies; reduce them into a single result. Use for multi-lens reviews, fastest-answer races, cross-provider consensus, or generate-and-pick.

<div class="aegis-term">
<div class="aegis-term-body"><span class="t-accent">⏺</span> aegis_group_spawn_mixed(name="audit", profiles=["sec","style","logic"])
<span class="t-accent">⏺</span> aegis_group_broadcast(name="audit", objective="audit PR #214", …)
<span class="t-accent">⏺</span> aegis_group_wait_all(name="audit", reducer="join_by_handle")
   └ <span class="t-success">3/3 replied · 92s · join_by_handle</span>

<span class="t-muted">--- result.reduced ---</span>
{"sec":     "no obvious issues; rate-limiter uses a safe defaults table…",
 "style":   "two nits in worker.py (naming, double-blank line)…",
 "logic":   "the retry guard mis-counts attempts on connection-reset…"}</div>
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

- **Multi-tab TUI.** N independent agent sessions plus terminal tabs. Generated alliterating handles (`lucid-knuth`, `wry-hopper`). State dots, sticky `*`, terminal bell when a backgrounded agent finishes. Per-block click-to-copy.
- **Honest metrics.** True input (including cache) with cached %, output, tool calls, per-turn and per-session timing. Provisional while streaming, exact at turn end. No log scraping anywhere.
- **Queue dashboard.** Always-on one-line strip above the status bar (per-queue depth, last in-flight worker). `Ctrl+D` for a full dashboard with in-flight / queued / recent bands and a live assistant-text tail.
- **File viewer + picker.** `Ctrl+O` opens a fuzzy file picker (typeahead, keyboard nav, top-match preselected) over a background watchdog index; selection lands in a `FileTab` — syntax-highlighted read-only view by default, `e` toggles edit mode, `Ctrl+S` saves, Escape with unsaved edits prompts to discard. Agents can drop you into the same view via the `aegis_view_file` MCP tool, and `Ctrl+click` on a backtick-wrapped filename in any agent response opens it directly.
- **Config panel.** `F2` opens the live `.aegis.yaml` editor inside the TUI — see agents/queues/telegram/plugin_dirs at a glance, add an agent through a validated modal. Same edit helpers back the scriptable `aegis config` CLI verbs, so a side-terminal `aegis config agent add` and the panel are interchangeable.
- **Session persistence.** `aegis` reopens the last workspace by default — agent tabs, terminal tabs, profiles, order, with each underlying session genuinely resumed. `aegis --clean` opts out.
- **Workflow catalog.** The `aegis.workflows` package ships seed workflows you import to register: `brainstorm_to_spec`, `execute_plan`, `review_branch`, `tdd_cycle`. The engine offers `ask_human` for host-tab dialogue, explicit checkpoints with durable resume, `spawn`/`close` for subagents, `bash_predicate` for retry-with-feedback loops, and `parallel` for fan-out joins.
- **Headless + Telegram.** `aegis serve` runs the SessionManager + MCP plane without a TUI. Add a Telegram token and drive your agent team from your phone.
- **Telegram substrate commands.** `/queue`, `/schedule`, `/budget`, `/peers`, plus session-spawn verbs — every aegis substrate is now reachable from the phone, with optional `@<peer>` for cross-host inspection. See [Telegram](telegram.md).
- **Remote plane.** Run `aegis serve` on a second machine and an agent on the first can `aegis_enqueue(target="builder", …)` to hand long work off over HTTP. Bind the plane wherever it should be reachable from — typically a private overlay network (Tailscale/Headscale/WireGuard/VPN) — and layer bearer-token and source-IP gates on top. Optional wire callbacks deliver the remote worker's final message back to the originating agent's inbox, and a small `/remote/v1/schedule/*` control plane lets an agent push, list, inspect, or remove schedules on a peer (and self-schedule its own future work). See [Remote plane](remote.md).
- **Per-queue budgets.** Declare USD or output-token ceilings over rolling windows on any queue; the substrate rejects new enqueues that would land the queue over budget, naming the binding constraint. Pull-only observability via CLI, MCP, HTTP. See [Budgets](budget.md).
- **MCP plane.** Every spawned agent is injected with the aegis MCP server. Orientation (`aegis_meta`), session listing, handoff, queue dispatch, canvas ops, terminal ops, group broadcast/gather, workflow invocation — one consistent surface across providers.

</section>

<div class="aegis-final" markdown>

## Get aegis

```bash
pip install aegis-harness   # Python 3.13+
aegis                       # full-screen TUI; opens ConfigPanel
                            # if no .aegis.yaml is found
```

[Install →](install.md){ .aegis-button }
[Usage →](usage.md){ .aegis-button }
[Roadmap →](roadmap.md){ .aegis-button }

</div>
