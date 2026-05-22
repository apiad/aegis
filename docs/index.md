# Aegis

A multi-agent meta-harness for coding agents. Aegis sits **above** the
harness — it drives existing coding-agent CLIs (`claude`, `gemini`,
`opencode`) over their structured protocols, parses the event streams,
and re-renders them in a calm Textual TUI where many agents run side by
side. It adds a routing + delegation plane on top: queues, workflows,
an MCP server every spawned agent talks to, and an optional Telegram
front-end.

```
┌ aegis ───────────────────────────────────────────────┐
│ ● 1 lucid-knuth ·opus·   ● 2 wry-hopper ·gemini·  *  │
│                                                       │
│ › explain the retry logic                             │
│                                                       │
│ ⠹ Thinking… (3.2s)                                    │
│ ⏺ Read(worker.py)                                     │
│   └ ok                                                 │
│ The retry path lives in _run_turn …                   │
│                                                       │
│ lucid-knuth ·opus· opus·full   ↑128k (94% cached) ↓1k │
│ ───────────────────────────────────────────────────── │
│ › ask something…                                      │
└───────────────────────────────────────────────────────┘
```

## Why aegis

Modern coding-agent CLIs are excellent at one conversation. They are
poor at multi-conversation orchestration: switching between agents,
delegating sub-tasks, comparing models on the same problem, or letting
one agent ask another for help. Aegis is the layer that solves those
problems without replacing the underlying agent.

- **Same UX across providers.** Multi-turn, streaming, cancellation,
  per-session MCP injection — uniform across Claude Code, Gemini, and
  OpenCode. New providers slot in behind one driver seam.
- **Many agents, one terminal.** Tabs. Per-tab profiles. Generated
  alliterating handles (`lucid-knuth`, `wry-hopper`). Cross-tab
  signalling (state dot, sticky `*`, bell). Per-block copy-to-clipboard.
- **Inter-agent delegation.** First-class queues — any agent can
  `aegis_enqueue(queue, payload)` and get an automatic inbox callback
  when the worker finishes. Workflows orchestrate multi-step pipelines
  in plain Python. A `Ctrl+D` queue dashboard surfaces what's running
  across all queues; incoming handoffs and callbacks render as
  distinct blocks in the receiving agent's transcript.
- **Honest metrics.** True input (incl. cache) with cached %, output,
  tool calls, per-turn and per-session timing — never log-scraped.
- **Headless mode.** `aegis serve` runs the SessionManager + MCP plane
  without a TUI, optionally fronted by Telegram so you can drive agents
  from your phone.

## Where to go next

- [Install](install.md) — get aegis on your machine.
- [Usage](usage.md) — keys, tabs, what the screen shows.
- [Configuration](configuration.md) — the `.aegis.py` file.
- [Drivers](drivers.md) — Claude, Gemini, OpenCode side by side.
- [Queues](queues.md) — inter-agent delegation.
- [Canvas](canvas.md) — shared markdown blackboard.
- [Workflows](workflows.md) — Python orchestration.
- [MCP plane](mcp.md) — the tool surface every agent gets.
- [Architecture & Vision](architecture.md) — how the pieces fit.
- [API reference](api.md) — auto-generated from the source.
- [Roadmap](roadmap.md) — what's shipped, what's next.
