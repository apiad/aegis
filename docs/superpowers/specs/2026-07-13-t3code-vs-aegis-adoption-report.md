# T3 Code vs Aegis — Feature & Adoption Report

**Status:** reference (adoption analysis — feeds future design specs)
**Date:** 2026-07-13
**Source audited:** `pingdotgg/t3code` (shallow no-LFS clone, cloned on VPS + rsync'd source to playground `.playground/t3code-analysis/t3code`)
**Comparison base:** `repos/aegis/` at current main (v0.16.0)
**Method:** four parallel read-only subagents mapped provider/driver, orchestration/MCP/relay, persistence/streaming/contracts, and frontend/product. Every claim below is grounded in a file that was actually read; file:line cites live in the subagent transcripts.
**Companion:** [`2026-06-30-omnigent-vs-aegis-adoption-report.md`](2026-06-30-omnigent-vs-aegis-adoption-report.md) (the prior version of this exercise, against omnigent) and the Claude-driver billing research (2026-07-13 conversation).

---

## 1. The one framing that matters

**T3 Code is not a meta-harness in aegis's sense. It is a polished single-agent-per-thread GUI.** A "thread" is one human ↔ one coding agent. There is **no** agent-to-agent coordination anywhere in the codebase — no spawn/worker/committee/parallel/delegate/queue commands. Their "OrchestrationEngine" is an event-sourcing/CQRS command bus (decider → event → projection), **not** an agent orchestrator; their "checkpoint" is a git working-tree snapshot, not a coordination primitive.

So the comparison is asymmetric and it's important to be honest about it:

- **Aegis's entire coordination layer** — queues, inbox/handoff, groups (broadcast/gather), canvas, file-claims, workflows, cron scheduler, server-to-server remote plane — **has zero counterpart in T3 Code.** These are genuine aegis differentiators, reconfirmed.
- **What T3 does better is orthogonal to orchestration**: the *driver layer*, *streaming/persistence reliability*, the *native product surface*, and *remote access ergonomics*. That's where the ideas are.

T3's positioning: **cross-platform (web + native desktop + native mobile) control plane for driving one agent at a time, per repo, with strong git/VCS integration.** Aegis's: **terminal-first, single-user, multi-agent coordination substrate.** Different products that overlap on "drive coding-agent CLIs cleanly."

---

## 2. Category map

| Capability | Aegis | T3 Code |
|---|---|---|
| Multi-agent coordination (queues/groups/handoff/canvas/claims) | ✅ full substrate | ❌ none (single agent per thread) |
| Workflow engine + cron scheduler | ✅ | ❌ |
| Server-to-server mesh / remote plane | ✅ HTTP peer enqueue + schedule push | ❌ declines by design ("connect to ONE server") |
| MCP plane exposing coordination tools to agents | ✅ enqueue/handoff/group/canvas/claim | ⚠️ MCP server exists, but one toolkit (browser preview) |
| Plugin system (`@hook`/`@tool`/`@workflow`) | ✅ | ❌ |
| Terminal TUI | ✅ Textual | ❌ |
| Web UI | ✅ PWA over `aegis serve` | ✅ React/Vite (primary UI) |
| Native desktop app | ❌ | ✅ Electron, spawns server child, WSL/SSH/Tailscale |
| Native mobile app | ❌ | ✅ Expo RN + ~7.6k lines Swift/Kotlin, lock-screen widget |
| Harness: Claude Code | ✅ `claude -p` stream-json | ✅ **`claude-agent-sdk` in-process** |
| Harness: Codex | ❌ | ✅ **`codex app-server` JSON-RPC** |
| Harness: Cursor | ❌ | ✅ ACP |
| Harness: OpenCode | ✅ ACP | ✅ HTTP (`opencode serve`) |
| Harness: Gemini / lovelaice (native) | ✅ | ❌ |
| Driver protocol richness | ⚠️ ACP + stream-json | ✅ Codex app-server adds rate-limits, reasoning summaries, plan updates, model-reroute |
| Persistence | ⚠️ append-only JSONL replay | ✅ **SQLite, event-sourced/CQRS, 32 migrations** |
| Resumable streams after WS reconnect | ⚠️ per-handle replay + JS coalesce | ✅ **snapshot+delta, monotonic sequence cursor, gap-free** |
| Command idempotency | ❌ (marks in-flight interrupted) | ✅ command-receipt latch |
| Git worktree per session | ⚠️ manual (agent-sandbox spec designed, no plan) | ✅ **automatic** (`worktreePath` on the thread) |
| Turn-level checkpoint / restore | ❌ | ✅ hidden git refs per turn |
| Source-control providers (GH/GL/Bitbucket/Azure) — create PR/MR | ❌ | ✅ |
| Inline in-chat diffs + file/diff/preview panel | ❌ | ✅ |
| Unseen-completion badges (`completedAt > lastVisitedAt`) | ⚠️ basic sticky `*` in TUI | ✅ richer, mark-unread |
| Dev-server preview discovery | ❌ | ✅ port-scan + in-app preview |
| Shared typed schema (backend ↔ web ↔ mobile) | ❌ (Python dataclasses backend-only) | ✅ one Effect `Schema` `contracts` package |
| Remote access via Tailscale / SSH bootstrap | ⚠️ manual | ✅ `tailscale serve` + SSH-injected remote start |
| Multi-account per provider (work/personal) | ❌ | ✅ instance-vs-session split, `CODEX_HOME` shadows |

---

## 3. What aegis has that T3 doesn't (keep + deepen)

Reconfirmed differentiators — do not regress these:

- **The whole multi-agent substrate.** Queues, groups, canvas, claims, handoff, workflows, scheduler. T3 has none of it. This is aegis's reason to exist.
- **Server-to-server mesh.** T3 *explicitly declines* it (`docs/architecture/remote.md`: "Client connects to ONE T3 server, never to multiple"). Aegis's cross-machine `aegis_enqueue(target=peer)` + peer schedule push is a real capability T3 chose not to build. **This is where aegis's mesh ambition is genuinely differentiated** — see idea 5.
- **MCP-as-coordination-plane.** T3 runs an MCP server too, but its only toolkit is browser preview automation; agents can't coordinate through it. Aegis's plane *is* the coordination surface.
- **Terminal TUI + plugin system.** No analog.

---

## 4. What T3 does better / differently (the ideas live here)

### 4a. Driver layer — richer protocol, structured control

- **Claude via the Agent SDK, not `claude -p`.** They drive Claude in-process through `@anthropic-ai/claude-agent-sdk`'s `query()` async-iterable, giving structured `setModel` / `setPermissionMode` / `setMaxThinkingTokens` / `getContextUsage` and clean `interrupt()` — control aegis can't get from parsing a `claude -p` stream. (Ties directly to the 2026-07-13 billing research: Agent SDK usage draws from the subscription pool *today*, same as `-p`, and is the official/compliant path.)
- **Codex `app-server` (JSON-RPC over stdio) is a richer protocol than ACP or stream-json.** It pushes `thread/tokenUsage/updated` (last + cumulative, separate reasoning tokens, context window), `account/rateLimits/updated`, reasoning-summary deltas, `turn/plan/updated`, and `model/rerouted`. Aegis normalizes onto ACP + claude stream-json and sees none of the rate-limit / reasoning-summary / reroute signals.
- **Persistent *instance* vs ephemeral *session*.** A driver instance is a configured account/home (multi-account: Codex Work + Codex Personal via `CODEX_HOME` shadows); a session/thread is ephemeral under it, with a "continuation identity" gating which instance may resume a thread. Aegis has no account-multiplexing.
- **Thread rollback-by-N-turns + resume-with-graceful-fallback** (Codex `thread/resume` degrades to fresh `thread/start` on unknown-thread). Aegis resume is all-or-nothing.

### 4b. Streaming & persistence reliability (their stated #1 priority)

- **SQLite event-sourcing/CQRS**: append-only `orchestration_events` (indexed, per-stream optimistic-concurrency `UNIQUE(aggregate, stream, version)`) + read-model projections with a persisted **projection cursor** (`last_applied_sequence`) so boot replays only the tail, not the whole log. Aegis replays entire JSONL on every boot.
- **Gap-free resumable WS streams.** Each stream item is tagged `snapshot | event` with a monotonic `sequence`; on reconnect the client passes `afterSequence` and the server *attaches the live subscription before draining the catch-up replay* (so events during the replay window aren't dropped), then the client dedups by sequence. This is exactly the robustness aegis's PWA-over-flaky-link needs.
- **Command receipts** (`command_id PRIMARY KEY → result_sequence`) give idempotent command delivery — retried commands don't double-apply.
- **Git per-turn checkpoints** into hidden `refs/t3/checkpoints/<thread>/<turn>` (write-tree/commit-tree; restore via `git restore` + `git clean`; cached diffs). Per-turn undo of the agent's edits without polluting history.

### 4c. Product surface

- **Automatic git-worktree-per-session** (`worktreePath` on the thread contract → `git worktree add`). The session runs there instead of the main tree. Aegis's agent-sandbox worktree isolation is *designed but manual*.
- **Native desktop (Electron, spawns the server as a child with a restart loop) + real native mobile** (Expo RN + ~7.6k lines Swift/Kotlin, Ghostty terminal, native diff view, lock-screen agent-activity widget). Aegis has TUI + PWA only.
- **Source-control providers** — GitHub/GitLab/Bitbucket/Azure behind one abstraction: create PR/MR, checkout PR, create repo. **Inline in-chat diffs** + right-panel Files/Diffs/Preview/Terminal. **Unseen-completion badges** (`completedAt > lastVisitedAt`, per-thread `lastVisitedAt`, mark-unread). **Dev-server preview discovery** (port-scan `lsof` → in-app preview).

### 4d. Architecture hygiene

- **One shared Effect `Schema` `contracts` package** defines provider events + WS RPC + domain events + errors, consumed by server, web, and mobile. No backend-emit ↔ frontend-parse drift; refactors are compile-checked across the monorepo. Aegis has no shared contract (dataclasses backend-only; the web client re-parses in JS).
- **Protocol framing in reusable packages** (`effect-acp`, `effect-codex-app-server`) with pending-request maps and buffered line-splitting, separate from adapter logic.

---

## 5. Ranked ideas for aegis (the actionable output)

Honest value ratings for **aegis's** context (terminal-first, single-user, multi-agent). No stealing — these are concept-level adoptions of good ideas from open source.

1. **[HIGH] Gap-free resumable WS streams for `aegis serve`.** Add a monotonic sequence to emitted events; let the web/PWA reconnect with `afterSequence`, attach-live-before-replay, dedup by sequence. Directly serves aegis's stated reason for the web client (remote dev over a flaky link). Low lift over the existing per-handle JSONL — the JSONL *is* the event log; it just needs a global sequence + resume RPC.

2. **[HIGH] Unseen-completion badges.** Track `completedAt > lastVisitedAt` per conversation; show a pill on backgrounded TUI tabs + PWA. Aegis already has a sticky `*`; T3's semantics (per-thread last-visited, "Working/Completed/Pending-Approval" status pills, mark-unread) are richer and cheap. Closes a real gap for running many agents.

3. **[HIGH] A Codex `app-server` driver.** Aegis has no Codex harness at all (also flagged in the omnigent report). The app-server protocol is *richer than ACP* — adopting it adds rate-limit, reasoning-summary, plan-update, and precise token-usage signals to aegis's event stream and budget tracking. Biggest single capability add.

4. **[HIGH] Automatic git-worktree-per-session (opt-in).** Make worktree isolation a first-class property of a session/agent profile (auto-create on spawn, auto-remove on close), so multi-agent fan-out is safe by default. This *sharpens the existing `2026-05-27-agent-sandbox-design.md` spec* (designed, no plan) — T3 validates the approach and shows the minimal shape (`worktreePath` on the session).

5. **[MEDIUM] Tailscale-serve + SSH-bootstrap for mesh reachability.** T3's `ensureTailscaleServe()` + MagicDNS discovery + SSH-injected remote start is exactly the substrate aegis's mesh needs: each node advertises on the tailnet over WireGuard; peers resolve `node.tailnet.ts.net`; `aegis_enqueue(target=peer)` rides HTTPS with zero manual tunnel config; SSH-bootstrap reaches a *cold* peer that isn't running the daemon yet. **Aegis's differentiator vs T3: do server-to-server over that tailnet — the thing T3 explicitly declined.**

6. **[MEDIUM] Drive Claude via `claude-agent-sdk`.** Structured `setModel`/`getContextUsage`/`interrupt` beat parsing `claude -p`. The billing research clears the compliance/billing path (Agent SDK = subscription pool today, official). This also becomes the natural home for the deferred REPL-driver hedge — the Agent SDK is the "subscription-safe" driver the REPL spec was reaching for, without the PTY+transcript-tail machinery.

7. **[MEDIUM] Command receipts / idempotency keys.** A `command_id → result` latch so client command retries (submit, interrupt) don't double-apply. Cheap correctness win as the PWA grows.

8. **[MEDIUM] Turn-level git checkpoints.** Hidden `refs/aegis/checkpoints/<handle>/<turn>` snapshots for per-turn undo + cheap diff rendering. Natural fit once worktree-per-session (idea 4) lands.

9. **[MEDIUM] A shared schema contract (concept, not Effect).** One source (pydantic/JSON-Schema) for events + WS protocol, generating TS types for the web client. Kills backend↔frontend drift as the web surface grows. Not urgent, but pays compounding interest.

10. **[LOW] Persistent instance vs session (multi-account)**, **projection cursor for incremental boot**, **dev-server preview discovery in the PWA**, **presence/awareness beacon per node**. Nice-to-haves; revisit if a concrete need surfaces.

---

## 6. Honest caveats

- T3 is self-described "very very early WIP," not accepting contributions. Some docs are stale (`docs/architecture/providers.md` still says "Codex is the only implemented provider"; the code ships five drivers). Judge the code, not the docs.
- **No automatic agent-subprocess crash-restart** was found in any T3 driver — robustness is buffered line-splitting + termination-fails-all-pending in the protocol layer + Effect scope teardown, not respawn. So "reliability first" is about *stream/connection* durability, not *process* self-healing. Aegis's queue replay (marks in-flight `failed:interrupted`) is arguably a stronger process-crash story.
- The Effect-TS + CQRS + SQLite stack buys T3 real robustness but at a large complexity cost. Aegis should adopt the *ideas* (sequence-cursor resume, command receipts) without importing the machinery — a global sequence + a resume RPC over the existing JSONL gets most of idea 1's value at a fraction of the cost.

---

*Provenance: authored interactively on zion, 2026-07-13, from a four-subagent audit of the playground clone. Playground clone is disposable (`.playground/` is gitignored).*
