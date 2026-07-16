# Aegis

> **The programmable multi-agent meta-harness.**
>
> Drives Claude Code, Gemini CLI, and OpenCode in one terminal, gives
> them six primitives for working together, and lets you orchestrate
> them with deterministic Python workflows and scheduled jobs.

[![CI](https://github.com/apiad/aegis/actions/workflows/ci.yml/badge.svg)](https://github.com/apiad/aegis/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-apiad.github.io%2Faegis-blue)](https://apiad.github.io/aegis/)
[![PyPI](https://img.shields.io/pypi/v/aegis-harness.svg)](https://pypi.org/project/aegis-harness/)
[![Python](https://img.shields.io/badge/python-3.13+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

```
┌ aegis · 3 agents · ~/code/aegis ─────────────────────────────────────┐
│ ● 1 lucid-knuth ·opus·   ● 2 wry-hopper ·gemini·   ● 3 brisk-curie * │
│                                                                       │
│ › explain the retry path in worker.py                                 │
│                                                                       │
│ ⠹ Thinking… (3.2s)                                                    │
│ ⏺ Read(worker.py)                                                     │
│   └ ok                                                                 │
│ The retry path lives in _run_turn at line 142 …                       │
│                                                                       │
│ ⏺ aegis_handoff(target=wry-hopper)                                    │
│   └ delivered to wry-hopper                                           │
│                                                                       │
│ queues: tests ●1/2 ○0 ✓3 ✗0    last: brisk-curie                      │
│ lucid-knuth ·opus· opus·full   ↑128k (94% cached) ↓1k                 │
│ ───────────────────────────────────────────────────────────────────── │
│ › ask something…                                                      │
└───────────────────────────────────────────────────────────────────────┘
```

## What aegis is

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

## Six primitives for agent coordination

*— the multi-agent pillar.*

Each primitive has one verb and lands the same way in the receiving
agent's transcript: as a `✉` block with a sender tag, timestamp, and a
short body preview. One delivery channel, six wake patterns.

### `→` Inbox — send context to a peer

Any agent can hand off to any other live agent. Fire-and-forget; the
recipient gets a normal user-message turn tagged with the sender's
handle. Use when you want a *specific* peer to pick up where you left
off.

```python
aegis_handoff(target_handle="reviewer", from_handle="impl",
              context="PR ready at branch feat/x — please review")
# → reviewer's transcript:
#   ✉ from agent:impl · 17:42:03Z
#     PR ready at branch feat/x — please review
```

### `⏳` Queue — spawn a worker on demand

Enqueue a task to a named queue and the substrate spawns a fresh agent
of the queue's configured profile, runs the payload as its opening
turn, and (with `callback=true`) delivers the worker's final result
back to your inbox. Producer keeps working between enqueue and
callback. Generalizes delegation: parallelism, max-in-flight caps,
restart safety, all built in.

```python
aegis_enqueue(queue="review", payload="…full self-contained prompt…",
              from_handle="impl", callback=True)
# → {task_id: 01HK…, queued_position: 1}
# …minutes later, in impl's transcript:
#   ✉ from queue:review · task#01HK… · ok · 17:46:11Z
#     PR looks clean. Two nits flagged in the diff comments…
```

### `▦` Canvas — collaborate on a shared document

Open a shared markdown file. Multiple agents read it, write sections of
it, subscribe to it. Each write wakes every other subscriber with a
diff-aware notification. The classical blackboard pattern — terminal-
native, MCP-driven, file-backed (you can grep it, commit it, open it in
your editor).

```python
# PM
aegis_canvas_open(name="report-q3", file="vault/reports/q3.md",
                  from_handle="pm")
aegis_canvas_subscribe(name="report-q3", from_handle="pm")

# Researcher (in another tab, after a handoff)
aegis_canvas_write_section(name="report-q3", section="data",
                           content="Q3 numbers came in stronger…",
                           from_handle="researcher")
# → PM's transcript:
#   ✉ from canvas:report-q3 · 20:30:00Z
#     section "data" · written by agent:researcher (+18 / -3 lines)
#     ──
#     Q3 numbers came in stronger than projected…
```

### `▮` Terminal — share a live shell

Spawn a PTY-backed shell that any agent (or Alex) can run commands on,
send raw keystrokes to, and subscribe to. Command boundaries are
detected from OSC 133 shell-integration markers; every finalized
command lands in an append-only JSONL ledger and wakes subscribers
through the same `✉` channel.

```python
# PM
aegis_term_spawn(name="build", from_handle="pm")
aegis_term_subscribe(name="build", from_handle="pm")

# builder (after a handoff)
rec = aegis_term_run(name="build", cmd="pytest -q",
                     from_handle="builder")
# → PM's transcript:
#   ✉ from term:build · 14:03:25Z
#     $ pytest -q  · run by agent:builder
#     exit 0 · 4.20s
#     ──
#     6 passed in 4.18s
```

### `▣` Groups — broadcast and gather

Form a named committee of agents that share one inbox-fanout channel
and one in-flight broadcast slot. Send one structured four-field
question — `objective`, `output_format`, `tool_guidance`,
`boundaries` — collect N parallel answers, reduce them into a single
result. Use when the same question has multiple useful perspectives,
or when you want to *race* providers and keep the fastest.

```python
# spawn three reviewers with different lenses
aegis_group_spawn_mixed(name="audit", from_handle="pm",
    profiles=["sec_reviewer", "style_reviewer", "logic_reviewer"])

aegis_group_broadcast(name="audit",
    objective="audit PR #214 (branch feat/rate-limit)",
    output_format="bullet list, each item severity-tagged (high/med/low)",
    tool_guidance="prefer Read + Grep; avoid Bash and Edit",
    boundaries="report only — no patches, no commits")

# collect every reply, keyed by reviewer
result = aegis_group_wait_all(name="audit",
                              timeout=300,
                              reducer="join_by_handle")
# → result.reduced = {"sec_reviewer": "…", "style_reviewer": "…",
#                     "logic_reviewer": "…"}
```

Switching to `aegis_group_wait_any` returns on the first reply and (by
default) sends a passive `cancel` envelope to the losers — useful when
the cheapest acceptable answer wins. Built-in reducers: `concat`,
`join_by_handle`, `last_wins`, `majority_vote`; custom reducers
register one function. Groups also have YAML presets in
`.aegis.yaml` (`groups.presets.<name>.profiles: […]`) and a dedicated
TUI tab with Members / Current broadcast / Recent broadcasts panels.

**Reach for it when:** multi-lens code audit, fastest-answer racing,
cross-provider consensus, generate-and-pick (N candidates → one),
role-persona panels (PM / eng / UX react to the same proposal). Full
walk-through in [docs/groups.md](docs/groups.md).

### `⟳` Workflow — deterministic Python orchestration

When the dance has to be **reliable** — TDD loops, bug triage,
multi-step plans, anything where retries with feedback matter — wrap it
in a workflow. Plain Python at the top of the stack. Calls agents, runs
bash predicates, retries with feedback, captures structured output.

```python
@workflow("tdd-cycle")
async def tdd_cycle(engine, *, feature: str) -> str:
    impl = await engine.spawn("implementer")
    await engine.send(impl, f"Write a failing test for: {feature}")
    await engine.bash_predicate(
        f"pytest tests/ -k {feature} 2>&1 | grep -E 'FAIL|ERROR'",
        retry_with="The test should fail because the feature isn't built yet")
    await engine.send(impl, "Now implement it.")
    await engine.bash_predicate(
        f"pytest tests/ -k {feature}",
        retry_with="Tests are still failing. Output:\n{stdout}")
    reviewer = await engine.spawn("reviewer")
    return await engine.send(reviewer, "Final review of branch.")
```

Triggered by any agent: `aegis_run_workflow(name="tdd-cycle",
kwargs={"feature": "rate_limit"})`. Workflows sit at the top of the
stack — they span agents, they own the loop, they're the right tool
when the spec is "follow this exact procedure" rather than "figure
it out."

The `aegis.workflows` package ships four seed workflows registered on
import: `brainstorm_to_spec` (Q/A → spec doc), `execute_plan` (parse
plan → dispatch implementer per task with durable resume),
`review_branch` (parallel reviewer fan-out → report), and `tdd_cycle`
(predicate-driven TDD loop). See [docs/workflows.md](docs/workflows.md).

## What else is in the box

- **Multi-tab TUI.** Generated alliterating handles (`lucid-knuth`,
  `wry-hopper`) for agents, purpose names (`build`, `db`) for terminals.
  State dots, sticky `*`, terminal bell when a backgrounded agent
  finishes. Click any block to copy it.
- **Honest metrics.** True input (incl. cache) with cached %, output,
  tool calls, per-turn and per-session wall-clock. Provisional while
  streaming, exact at turn end. Live `ctx Nk (P%)` segment shows the
  current turn's true input against the model's context window — Opus
  4.x at 1M, Sonnet/Haiku at 200k, Gemini at 1M. **No log scraping
  anywhere.**
- **Queue dashboard.** Always-on one-line strip above the status bar
  shows live per-queue depth and the most recent in-flight worker.
  `Ctrl+D` expands into a full-screen modal with `QUEUES / IN-FLIGHT /
  QUEUED / RECENT` bands and a live assistant-text tail.
- **File viewer + picker.** `Ctrl+O` opens a fuzzy file picker
  (typeahead, keyboard nav, top-match preselected) over a background
  watchdog index; pick a file and it lands in a `FileTab` —
  syntax-highlighted read-only view by default, `e` toggles edit mode,
  `Ctrl+S` saves, Escape with unsaved edits prompts to discard. Agents
  can drop you into the same view via the `aegis_view_file` MCP tool,
  and `Ctrl+click` on a backtick-wrapped filename in any agent
  response opens it directly.
- **Config panel.** `F2` opens the live `.aegis.yaml` editor inside
  the TUI — see agents/queues at a glance, add an agent
  through a validated modal. Same edit helpers back the scriptable
  `aegis config` CLI verbs and a parallel MCP surface
  (`aegis_config_add_agent`, `…_add_queue`, `…_add_plugin_dir`,
  `…_set_schedule_enabled`, plus removes and reads), so agents can
  extend the substrate from inside — declare a queue and enqueue to
  it within one session, no restart. Panel, CLI, and MCP all route
  through the same comment-preserving atomic-write path.
- **Session persistence.** `aegis` reopens the last workspace by
  default — agent tabs, terminal tabs, profiles, order, with each
  underlying session genuinely resumed (model memory intact).
  `aegis --clean` opts out.
- **Workflow catalog.** `aegis.workflows` ships four ready-to-use
  seeds (`brainstorm_to_spec`, `execute_plan`, `review_branch`,
  `tdd_cycle`); importing them registers. Engine offers `ask_human`,
  explicit `checkpoint` + durable resume, `bash_predicate` retry
  loops, and `parallel` fan-out.
- **Headless + web.** `aegis serve` runs the SessionManager + MCP
  plane without a TUI; add a `web:` token to drive the team from an
  installable, mobile-first web/PWA client (`aegis web`).
- **MCP plane.** Every spawned agent is injected with the aegis MCP
  server: orientation (`aegis_meta`), session listing, handoff, queue
  dispatch, canvas ops, terminal ops, group broadcast/gather, workflow
  invocation. One consistent surface across providers. With
  `--strict-mcp-config`, aegis is the *only* MCP server the spawned
  agent sees.
- **Driver parity.** The canonical event surface unifies what every
  substrate publishes: semantic tool kinds (📖 read / ✏️ edit / ⌬
  execute / 🔎 search / ✻ think / 🌐 fetch) with locations + raw
  inputs, file diffs for edits, plan blocks for `TodoWrite` /
  `AgentPlanUpdate`, mid-turn cost / mode / title telemetry, and
  end-of-turn `stop_reason` / `cost_usd` / per-model attribution.
  Same render code paths for Claude, Gemini, and OpenCode; opencode's
  per-token thought stream coalesces by `message_id` into one block
  per assistant message.
## Plugins — extend aegis without forking

Plugins are how third-party code adds behavior to a session. Three
composable primitive shapes, auto-imported from disk, installable from
anywhere over `gh:` registry URLs:

- **`@hook(event)`** — fires on harness lifecycle events. `pre_turn`
  is the mutator (prepend system text, rewrite the user message,
  block the turn); `post_turn` / `session_start` / `session_end` are
  observers. Composes deterministically; timeout-wrapped per call;
  JSONL-logged.
- **`@tool`** — first-class FastMCP tools the spawned agent can call.
  Schema is auto-generated from type hints + docstring. Reserved
  names (every built-in `aegis_*`) are guarded at registration.
- **`@workflow`** — orchestrated procedures driven by the
  `WorkflowEngine` (delegate, spawn, send/drain, bash, groups). CLI-,
  MCP-, or scheduler-invoked.

A plugin is one directory: a `plugin.toml` manifest, the Python
module(s) holding decorated functions, and optional `_install.py` /
`_uninstall.py` for setup and teardown. The loader recurses and
auto-imports `*.py`, **skipping** anything starting with `_` — so
install scripts coexist with runtime code without colliding.

Install lifecycle:

```bash
aegis plugin install <name> --from gh:owner/repo#plugins/<name>
aegis plugin list / show / update / search / uninstall
```

`--from` accepts `gh:owner/repo[@ref][#path]` (HTTPS `git archive`
fetch) or `file:///abs/path` (local copy). Without `--from`, the
default registry is `gh:apiad/aegis#plugins/`. Installs roll back on
failure; `.aegis/plugins.lock` records the resolved source +
timestamp.

### Two canonical plugins (in this repo)

**`skill-system`** — Claude-Code-style skill selection on any
harness. A `pre_turn` hook injects a numbered menu parsed from
`.aegis/skills/*.md`; a `load_skill(name)` `@tool` pulls the body on
demand. ~100 lines of Python.

```bash
aegis plugin install skill-system --from gh:apiad/aegis#plugins/skill-system
```

**`memory-system`** — Hermes-inspired persistent memory with
periodic dreaming. Per-project `.aegis/memory/` holds a user-edited
`SOUL.md` + `USER.md`, a `MEMORY.md` index over typed entries (`user`
/ `feedback` / `fact` / `reference`), and a `dreams/` log. The
`pre_turn` hook injects the persona bundle on turn 0 and top-K
relevant teasers on later turns. Five `memory_*` `@tool`s let the
agent read/write/search. The `dream` `@workflow` runs a three-stage
consolidate-plus-synthesize pass over the last week of session
transcripts; install asks once whether to schedule it daily at 03:00.
Exercises every substrate primitive (`@hook`, `@tool`, `@workflow`)
end-to-end.

```bash
aegis plugin install memory-system --from gh:apiad/aegis#plugins/memory-system
```

Full protocol reference: [Plugins documentation](https://apiad.github.io/aegis/plugins/).

## Remote plane — cross-machine handoff

`aegis serve` can expose a second HTTP plane — distinct from the
loopback MCP plane, bound wherever you want it reachable — that other
`aegis serve` instances POST into. One agent on one machine can hand
a long task off to another without leaving the substrate:

```python
aegis_enqueue(
    queue="implementation",
    payload="Implement the design at <path> with TDD…",
    from_handle="lucid-knuth",
    target="builder",           # ← new — routes to a remote aegis
)
# → {task_id: "01J…", target: "builder",
#    callback_note: "no wire return channel in v1; completion behavior
#                    is whatever the receiving serve is configured to do"}
```

Configuration lives in `.aegis.yaml`. Outbound — the peers this serve
can call:

```yaml
remotes:
  builder:
    url: http://100.64.0.5:8556
    # token: "<optional bearer>"
```

Inbound — opt-in receive side; default off:

```yaml
remote_plane:
  bind: 100.64.0.5:8556         # the address to listen on
  accept_tokens: []             # optional bearer-token allowlist
  accept_from: []               # optional source-IP allowlist
```

Bind the plane wherever it should be reachable from, and only from —
typically a private overlay network (Tailscale/Headscale/WireGuard/
VPN) so the network itself is the trust anchor. Bearer-token and
source-IP gates compose with AND on top. By default the call is
fire-and-forget: the receiving serve runs the worker under its own
config and whatever it does on completion (commit and push, message
through its own bridge, write to a shared folder, nothing) is up to
it.

Since v0.8.0, `aegis_enqueue(target=…, callback=True)` opts in to a
**wire callback** that delivers the remote worker's final message
back to the originating agent's inbox as a normal `✉ from
queue:<peer>:<name>` envelope (symmetric peers config required —
both sides define each other in `remotes:`). A small
`/remote/v1/schedule/*` control plane (with `aegis_schedule_*` MCP
tools and `aegis schedule push --to <peer>` / `--remote <peer>` CLI
verbs) lets one serve push schedules into a peer and inspect or
remove them remotely — useful for self-scheduling future work or
managing a fleet from one host. Full surface, error model, and
patterns in [docs/remote.md](docs/remote.md).

## Per-queue budgets

*— the programmable pillar.*

Declare rolling USD or output-token ceilings on any queue. The
substrate enforces them at enqueue time — if admitting the task would
push the queue over any configured ceiling, the enqueue is rejected
immediately with a structured error that names the blocking constraint
and gives an ETA for when the queue will unblock.

```python
# .aegis.py
queues = {
    "impl": {
        "agent": "opus",
        "max_parallel": 2,
        "budgets": [
            {"usd": 1.00,             "window": "1h"},
            {"usd": 10.00,            "window": "24h"},
            {"output_tokens": 500000, "window": "1h"},   # runaway belt
            {"usd": 50.00,            "window": "7d"},
        ],
    },
    "fast": {
        "agent": "haiku-fast",
        "max_parallel": 4,
        # no budgets: key → no caps; behaves as before
    },
}
```

All-must-allow: a task is admitted only if every budget entry is
under its ceiling. When rejected, the error names every blocking
constraint, how much was spent vs the limit, and an `unblock_at` ETA.

Budget state is visible via `aegis budget list/show` (with `--remote
<peer>` for cross-host), the `aegis_budget_status` MCP tool, and the
read-only `GET /remote/v1/budget` endpoints on the remote plane. No
alerts — observability is pull-only; the rejection at enqueue time is
the only loud signal.

See [docs/budget.md](docs/budget.md) for the full model.

## Scheduled workflows

*— the programmable pillar.*

Aegis runs a cron-style scheduler alongside QueueManager and the inbox
router. Schedules are declared in `.aegis.yaml` and can be split into
drop-in overlays under `.aegis/schedules/<name>.yaml`. Each entry names
a workflow (built-in or registered), a trigger (`cron` or `fire_at`),
a lifecycle (`forever` / `once` / `{fires: N}` / `{until: <iso>}`),
and an overlap policy (`skip` / `queue` / `kill`).

```yaml
# .aegis.yaml
schedules:
  morning-briefing:
    workflow: prompt
    cron: "0 6 * * *"
    timezone: America/Havana
    args: { agent: default, message: "Write today's briefing." }
  ci-watch:
    workflow: enqueue
    cron: "*/5 * * * *"
    lifecycle: forever
    on_overlap: skip
    args: { queue: ci, payload: "Check CI status and report failures." }
```

Two workflows ship in-tree: `prompt` (one-shot agent message) and
`enqueue` (scheduler → queue handoff).

The substrate writes a JSONL audit log per schedule under
`.aegis/state/schedules/<name>.jsonl` plus a derived
`schedules.snapshot.json` for dashboards. On boot it replays each log
to rebuild fire counts and closes any dangling `fire_requested` record
as `failed:interrupted`. Editing `.aegis.yaml` or any overlay file
hot-swaps the schedule table without a restart — entries that didn't
change keep their state.

```bash
aegis schedule list                # current schedules + next fire
aegis schedule show morning-briefing
aegis schedule run morning-briefing   # force-fire once
aegis schedule disable morning-briefing  # comment-preserving YAML edit
aegis schedule logs morning-briefing -n 50
```

## Install

```bash
pip install aegis-harness        # or: uv pip install aegis-harness
```

Requires Python 3.13+ and at least one of: `claude`, `gemini`, or
`opencode` on your `PATH`, signed-in.

## Quickstart

```bash
aegis          # full-screen TUI — first-class UI for local dev
aegis web      # installable PWA — first-class UI for remote (and local) dev
```

aegis has **two co-equal first-class UIs** over one backend: the
full-screen **TUI** for local development, and an installable, mobile-first
**web/PWA** client for remote development over a flaky link (and locally
too). Both render the same transcripts with identical fidelity; sessions
are shared across them. `aegis web` ensures a token, opens your browser,
and serves the client.

With no `.aegis.yaml` in the directory, `aegis` drops you straight
into the TUI ConfigPanel — press `a` to add your first agent and
save. The scriptable equivalent:

```bash
aegis config agent add main --provider claude-code \
                            --model opus --effort high
aegis                       # start the TUI normally
```

`.aegis.yaml` is declarative YAML — edit it by hand or use
`aegis config` verbs (every section reachable: agents, queues,
default-agent, plugin-dir). Mid-session, reach the
ConfigPanel via `F2`.

## Keys

| Key | Action |
|---|---|
| `Enter` | Send |
| `!cmd` | **Shell escape** — run `cmd` locally, inject its output as your message |
| `/cmd` | **Slash command** — aegis runs it directly (`/help` lists them); never reaches the agent |
| `Ctrl+T` / `Ctrl+N` | New tab (default agent) / new tab (pick agent) |
| `Ctrl+E` | New terminal tab (`term:<name>`) |
| `Ctrl+W` | Close tab (last → quit) |
| `Ctrl+1`..`9` / `Ctrl+Tab` / `Ctrl+←→` | Switch tabs |
| `Ctrl+K` | Toggle terminal-tab input between **run** and **raw** mode |
| `Ctrl+D` | Open / close the queue dashboard |
| `F2` | Open the **ConfigPanel** — edit agents/queues/etc. live |
| `Escape` | Interrupt the active turn (or dismiss a modal) |
| `Click on a block` | Copy that message / tool result to clipboard |
| `Ctrl+Q` | Quit |

A backgrounded tab that finishes shows a `*` and rings the bell.

## Configuration

`.aegis.yaml` is declarative YAML. Author it interactively (the TUI
ConfigPanel — boot-into-panel when no config exists, `F2` mid-session)
or with the scriptable CLI (`aegis config agent add`, `aegis config
queue add`, `aegis config default-agent`, …). Shape:

```yaml
default_agent: default

agents:
  default:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
  reviewer:
    provider: claude-code
    model: sonnet
    permission: read
  fast:
    provider: gemini
    model: gemini-3-flash-preview
    permission: full
  oss:
    provider: opencode
    model: opencode/kimi-k2.6
    permission: full

queues:
  review:
    agent: reviewer
    max_parallel: 2
  fast:
    agent: fast
    max_parallel: 4
```

Full reference: [Configuration](https://apiad.github.io/aegis/configuration/).

## Headless + web

`aegis serve` runs the SessionManager and MCP plane without the TUI; add
a `web:` block to serve the installable, mobile-first web/PWA client and
drive the team from any browser:

```yaml
# .aegis.yaml
web:
  bind: 127.0.0.1          # front with a reverse proxy for remote access
  port: 8899               # omit to auto-pick
  # token: "…"             # or set AEGIS_WEB_TOKEN (env wins) — keeps it out of git
```

`aegis web` ensures a token, opens your browser, and serves. The TUI, the
web client, and (eventually) a remote TUI all speak the same WebSocket
protocol over one backend, so sessions are shared across them. A systemd
unit template lives at `scripts/aegis-serve.service`.

## Docs

Full documentation: **[https://apiad.github.io/aegis/](https://apiad.github.io/aegis/)**

- [Install](https://apiad.github.io/aegis/install/)
- [Usage](https://apiad.github.io/aegis/usage/)
- [Configuration](https://apiad.github.io/aegis/configuration/)
- [Drivers](https://apiad.github.io/aegis/drivers/) — Claude / Gemini / OpenCode
- [Queues](https://apiad.github.io/aegis/queues/) — inter-agent delegation
- [Canvas](https://apiad.github.io/aegis/canvas/) — shared markdown blackboard
- [Terminals](https://apiad.github.io/aegis/terminals/) — shared live PTY
- [Groups](https://apiad.github.io/aegis/groups/) — broadcast-and-gather committees
- [Remote plane](https://apiad.github.io/aegis/remote/) — laptop ↔ VPS enqueue over HTTP
- [Workflows](https://apiad.github.io/aegis/workflows/) — Python orchestration + catalog
- [Budgets](https://apiad.github.io/aegis/budget/)
- [MCP plane](https://apiad.github.io/aegis/mcp/) — the tool surface
- [Architecture](https://apiad.github.io/aegis/architecture/)
- [Roadmap](https://apiad.github.io/aegis/roadmap/)
- [API reference](https://apiad.github.io/aegis/api/)

## Status

Beta. Personal-infrastructure-grade, evolves fast. Expect change before
1.0. See the [roadmap](https://apiad.github.io/aegis/roadmap/) for
what's next.

## License

MIT — see [LICENSE](LICENSE).
