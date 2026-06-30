# Omnigent vs Aegis — Feature Parity & Adoption Report

**Status:** reference (adoption analysis — feeds future design specs)
**Date:** 2026-06-30
**Source audited:** `omnigent-ai/omnigent` (committed to playground at `.playground/omnigent/`)
**Comparison base:** `repos/aegis/` at current main
**Provenance:** authored by a VPS telegram-batch job on 2026-06-30; promoted here from
`vault/+/agent_drafts/telegram-replies/reply-batch_2026-06-30T11-12-45Z.md`.

---

## 1. Quick orientation — what omnigent is

Omnigent (`omnigent-ai/omnigent`, not Databricks — the Databricks connection is that their Mosaic AI workspace is one supported LLM credential gateway) is the closest publicly active project to aegis: both self-describe as "meta-harnesses" that drive coding agents rather than replacing them. The similarity is deep enough that feature comparison is meaningful without much hand-waving.

Omnigent's positioning is **cloud-first, team-first, multi-device**. Aegis's is **terminal-first, programmable, single-user personal infrastructure**. That divergence shapes which gaps are worth closing and which aren't.

---

## 2. Feature parity table

| Capability | Aegis | Omnigent |
|---|---|---|
| Terminal TUI | ✅ Textual, rich | ❌ CLI wrapper only |
| Web UI | ❌ | ✅ Full browser + mobile |
| Desktop app | ❌ | ✅ macOS |
| Multi-user / auth / OIDC | ❌ | ✅ |
| Session sharing / co-drive / fork | ❌ | ✅ |
| Harness: Claude Code | ✅ | ✅ |
| Harness: Gemini CLI | ✅ (ACP) | ✅ (Antigravity) |
| Harness: OpenCode | ✅ (ACP) | ✅ |
| Harness: Codex | ❌ | ✅ |
| Harness: Cursor | ❌ | ✅ |
| Harness: Hermes / Pi / Kimi / Qwen / Copilot / Kiro / Goose | ❌ | ✅ all |
| Inbox / handoff | ✅ `aegis_handoff` | ✅ `sys_session_send` |
| Queue / worker dispatch | ✅ `aegis_enqueue` | ✅ `sys_session_send` |
| Canvas (shared markdown blackboard) | ✅ | ❌ |
| Terminal tabs (shared PTY) | ✅ | ✅ (`sys_terminal_*`) |
| Groups (broadcast + gather) | ✅ | ❌ |
| Plugin system (`@hook`/`@tool`/`@workflow`) | ✅ | ❌ |
| Scheduled workflows (cron) | ✅ full YAML cron | ✅ |
| Declarative agent YAML (`omnigent run agent.yaml`) | ❌ | ✅ |
| Session forking | ❌ | ✅ |
| Budget caps (rolling USD/token windows) | ✅ | Partial (session-level caps) |
| Policy system (ALLOW/DENY/ASK per tool call) | ❌ | ✅ 3-level composition |
| Per-turn LLM cost advisor (auto model selection) | ❌ | ✅ |
| Cloud sandbox provisioning (Modal/Daytona/E2B/etc.) | ❌ | ✅ |
| OS-level sandboxing (bubblewrap/seatbelt) | ❌ | ✅ |
| Git worktree per subagent (fanout pattern) | ❌ (manual) | ✅ (via skills) |
| Remote plane (cross-machine enqueue over HTTP) | ✅ | ❌ |
| Telegram integration | ✅ full command surface | ❌ |
| Session persistence (genuine resume) | ✅ | ❌ |
| Config panel (live TUI editing) | ✅ F2 | ❌ |
| Driver parity (unified event surface) | ✅ semantic tool kinds | Partial |

---

## 3. What aegis has that omnigent doesn't

These are genuine differentiators worth keeping and deepening:

**Canvas.** Shared markdown blackboard with live notify. Omnigent has no equivalent. For long-running multi-agent tasks where agents need to co-author a working document this is uniquely useful.

**Groups.** Broadcast-and-gather over a committee of agents. Omnigent's model is always orchestrator → one worker. Aegis's groups are a distinct coordination shape with no analog in omnigent.

**Plugin system.** The `@hook`/`@tool`/`@workflow` triad with `gh:` registry URLs, `_install.py` lifecycle, and reserved name guards is a real extension architecture. Omnigent's customization is limited to YAML-declared Python callables as policy handlers or tools — no composable hook layer.

**Remote plane.** Cross-machine enqueue with wire callbacks and peer schedule push is genuinely absent from omnigent. Omnigent runs one server and sends agents to cloud sandboxes it provisions; aegis federates between independently-running instances over HTTP.

**Telegram.** Omnigent has a web UI that works on mobile. Aegis routes everything through a bot with substrate commands. Different UX but aegis's is more powerful for power users who live in Telegram.

**Rolling budget windows with rejection.** Aegis's per-queue USD and token windows with `unblock_at` ETA and structured rejection errors are more sophisticated than omnigent's session-level spend caps. Omnigent's `cost_budget` policy just hard-stops at a limit.

**Session persistence (genuine resume).** Omnigent restores the session tree but the underlying harness starts fresh. Aegis resumes the actual underlying agent conversation with model memory intact. This is a hard architectural advantage.

**TUI.** Omnigent has no interactive terminal UI — it wraps the harness terminal and adds a web layer. Aegis's Textual TUI with tabs, dashboard, ConfigPanel, queue monitoring, and groups dashboard is a unique surface.

---

## 4. What omnigent has that aegis lacks — and what to do about it

### 4a. Policy system ★★★ HIGH VALUE

**What it is:** Three-level (server / agent-spec / session) composition of policy callables. Each policy evaluates a `PolicyEvent` (tool name + args + session state) and returns ALLOW, DENY, or ASK. ASK pauses the agent and parks an async future waiting for a human approval from the UI (24h default timeout). Multiple policies run in declaration order; first DENY short-circuits.

**Built-in policies:** `ask_on_os_tools` (pause before shell/file writes), `max_tool_calls_per_session` (rate limit), `cost_budget` (hard spend cap), `github_policy` (repo/branch allowlists), `gdrive_policy`.

**How it integrates:** A `native_policy_hook.py` intercepts every tool call through the harness bridge — for Claude's `Bash`, `Write`, `Edit` etc. The hook maps harness-native names to the policy event and applies the verdict before forwarding to the harness.

**What aegis should adopt:** A hook-level policy gate — one `@hook(pre_turn)` that fires before every tool call with ALLOW/DENY/ASK semantics. This fits naturally into the existing plugin system: a `policy-system` plugin on top of `@hook(event="pre_tool")`. The three-level composition (session > agent > server) maps to: session-level MCP config, `.aegis.yaml` declared policies, and an aegis-wide `policies.yaml`. ASK could surface in Telegram (pause for approval) or TUI (modal prompt). This would give aegis genuine governance without breaking the ethos — declarative, pluggable, not hardcoded.

### 4b. Declarative agent YAML (`omnigent run agent.yaml`) ★★ MEDIUM VALUE

**What it is:** A self-contained spec that declares harness, model, auth, tools (MCP/Python/sub-agents), policies, OS access, and system prompt. `omnigent run path/to/agent.yaml` spins up that agent. Sub-agents are declared inline as `type: agent` tools, turning an orchestrator spec into a tree of agents defined in one file (or a directory of files, as Polly is).

**Why it's interesting for aegis:** Aegis's `.aegis.yaml` is a session config — agents, queues, plugins. But it doesn't have a "run this spec now" primitive. Adding `aegis run spec.yaml` (or `aegis workflow run spec.yaml`) would let users share reusable agent configs (like Polly) as portable YAML without having to modify the workspace's `.aegis.yaml`. This is additive — it doesn't replace the existing config, just extends the entry point.

**Implementation hint:** The spec concept could reuse aegis's existing `AgentProfile` concept. A spec YAML maps directly to a profile + queue definition that gets registered ephemerally for the session's duration.

### 4c. Per-turn LLM cost advisor ★★ MEDIUM VALUE

**What it is:** On each user turn, a cheap LLM call (the cheapest configured model) classifies the turn's difficulty (trivial / medium / difficult) using a few-shot rubric, then selects the appropriate model tier for that turn. The orchestrator's brain model changes dynamically per turn, not per session. In "advise" mode it records the verdict without applying; in "optimize" mode it actually overrides the model.

**Why it matters:** Aegis's budget system is about hard cost *caps* — reject when over limit. Omnigent's cost advisor is about *efficiency* — don't spend opus tokens on "thanks, continue". They're complementary. The advisor reduces waste proactively; the budget is the backstop.

**What aegis should adopt:** A `cost-advisor` plugin with a `@hook(pre_turn)` that makes a cheap classification call and either logs the suggestion (advise mode) or injects `model_override` into the turn config (optimize mode). The LLM judge call is ~10-20 tokens; the savings on a long session with trivial turns can be 10x+. This fits naturally into the existing budget system as a "soft enforcement" layer below the hard cap.

### 4d. OS-level sandboxing (bubblewrap / seatbelt) ★★ MEDIUM VALUE

**What it is:** Omnigent wraps every agent subprocess in OS-level sandboxing: `bwrap` on Linux (filesystem namespace, network policy), `seatbelt` profiles on macOS (Apple's builtin). The sandbox is declared per-agent in the YAML (`sandbox.type: linux_bwrap`, `write_paths`, `allow_network`). The default sandbox type is auto-selected by platform.

**Gap in aegis:** Aegis permissions are at the harness level (Claude Code's own `--permission` flag). There's no OS-level isolation of what the spawned agent process can touch outside its declared scope. For aegis's multi-agent model where a queue worker is given broad autonomy, a bwrap shell that confines writes to the declared working directory would add a meaningful safety layer.

**What aegis should adopt:** A `sandbox` config key in queue profiles and agent specs. On Linux, the driver's `subprocess.Popen` call gets wrapped with a `bwrap` command built from the declared `write_paths`. On macOS, the same with `sandbox-exec -f <profile>`. The implementation is ~150 lines (the spec parsing + the command builder). It would be an opt-in queue-level config key, not mandatory.

### 4e. Session forking ★ LOW-MEDIUM VALUE

**What it is:** `omnigent run --fork <session_id>` clones a conversation's history at the fork point and starts a new session from there, on the same or a different host. Useful for exploring alternate approaches from a branching point.

**Why it matters for aegis:** With session persistence, aegis can genuinely resume sessions. Forking would let you say "from this checkpoint, try two different approaches" and compare results. Currently aegis's workflow engine does something similar with `checkpoint` + durable resume, but at the workflow level, not the agent-conversation level.

**What aegis should adopt:** A `aegis fork <handle>` command that snapshots the current agent's conversation state and starts a new tab from that snapshot. Lower priority — the workflow engine's `parallel` already handles the branching case at a higher level.

### 4f. Harness breadth expansion ★ MEDIUM VALUE (tactical)

**What it is:** Omnigent supports Codex, Cursor, Hermes, Pi, Kimi (Moonshot AI), Qwen, GitHub Copilot, Kiro, Goose, and Antigravity in addition to Claude/Gemini/OpenCode. Each is driven via its native CLI in ACP or stream-json mode, or via a dedicated SDK.

**What aegis should adopt:** Codex ACP is most immediately useful given its open-source nature. Kimi (`kimi --print --output-format stream-json`) and Qwen (`qwen --acp`) follow the exact same ACP pattern as Gemini and OpenCode — adding them to aegis's ACP driver is probably a few dozen lines of integration. Pi has its own native driver but the pattern is established. Goose (Block's OSS agent) and Kiro (Amazon's new agent) would be stretch goals.

### 4g. Fanout-with-worktrees pattern (codified) ★★ MEDIUM VALUE

**What it is:** Polly's `fanout` skill codifies a pattern: for N independent parallel tasks, create N git worktrees, dispatch N workers each scoped to their own worktree, collect via inbox, cross-review (different vendor than implementer), and PR per task. The supervisor never merges. The pattern is implemented as a skill file, not as a core primitive.

**Gap in aegis:** Aegis has `parallel` in the workflow engine, and queues that spawn workers. But there's no built-in "worktree per worker" helper. Each worker gets the same cwd as the parent. If two workers touch the same files in the same cwd they conflict.

**What aegis should adopt:** A workflow primitive `spawn_worktrees(tasks, queue)` that: (1) creates a git worktree per task at `.aegis_worktrees/<task_id>/`, (2) enqueues each task with the worktree path injected into the payload, (3) returns task handles. This is about 50 lines in the workflow engine and would make the Polly-style pattern a first-class aegis workflow. An alternative is a `memory-system`/`skill-system`-style plugin that teaches the agent this pattern via a skill injection — but a primitive is cleaner for deterministic orchestration.

---

## 5. What omnigent does NOT fit aegis's ethos

**Web UI / desktop app / multi-user.** Aegis is terminal-first, single-user personal infrastructure. Building a web layer would require a server, auth, persistence layer, and a whole React codebase. The Telegram + TUI combination already covers aegis's "phone + desktop" use case. Skip.

**OIDC / team auth / invite links.** Same — single-user ethos. Skip.

**Managed cloud sandbox provisioning.** Aegis's remote plane + VPS setup already handles the "run on a persistent machine" case. Provisioning ephemeral Modal/Daytona sandboxes per session is a product feature for SaaS deployments. Skip.

**Session sharing / co-drive.** Multiplayer agent sessions. Not in scope for a personal harness. Skip.

---

## 6. Prioritized adoption roadmap

| Priority | Feature | Effort | Payoff |
|---|---|---|---|
| 1 | **Policy system** (plugin form: `@hook(pre_tool)` + ALLOW/DENY/ASK) | M (1-2 weeks) | High — governance primitive missing entirely |
| 2 | **Cost advisor** (LLM judge + `@hook(pre_turn)` model override) | S (2-3 days) | High — reduces wasted opus spend immediately |
| 3 | **Fanout worktree primitive** (`spawn_worktrees` in workflow engine) | S (1 day) | Medium — makes parallel coding workflows safer |
| 4 | **Declarative agent YAML** (`aegis run spec.yaml`) | M (1 week) | Medium — portability and shareability |
| 5 | **OS-level sandboxing** (bwrap/seatbelt queue opt-in) | M (1 week) | Medium — safety for autonomous queue workers |
| 6 | **Codex / Kimi / Qwen drivers** (ACP extension) | S (2-3 days each) | Low-medium — harness breadth |
| 7 | **Session forking** (`aegis fork <handle>`) | M (1 week) | Low — already covered by workflow `parallel` |

---

## 7. TL;DR

Omnigent is broader (more harnesses, web UI, team features, cloud sandboxes), aegis is deeper (richer TUI, canvas, groups, plugin system, remote plane, rolling budgets, genuine session persistence, Telegram). The two most impactful things to steal are:

1. **The policy system** — plug-in ALLOW/DENY/ASK gate at the tool-call level. Aegis has budget caps but no tool-level approval flow. This is a 3-level composition (session/spec/global) and fits naturally as a `policy-system` plugin using the existing `@hook` primitive.

2. **The LLM cost advisor** — a cheap per-turn classifier that auto-selects model tier based on turn difficulty. Complementary to (not a replacement for) aegis's existing budget caps. 10-50x cost reduction in practice on sessions with a mix of trivial and complex turns.

Everything else is either already better in aegis, off-ethos, or a smaller win.

---
