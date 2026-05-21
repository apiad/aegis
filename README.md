# Aegis

> A multi-agent meta-harness for coding agents — drives Claude Code,
> Gemini CLI, and OpenCode side by side in one calm full-screen TUI.

[![CI](https://github.com/apiad/aegis/actions/workflows/ci.yml/badge.svg)](https://github.com/apiad/aegis/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-apiad.github.io%2Faegis-blue)](https://apiad.github.io/aegis/)
[![PyPI](https://img.shields.io/pypi/v/aegis-harness.svg)](https://pypi.org/project/aegis-harness/)
[![Python](https://img.shields.io/badge/python-3.13+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Aegis sits **above** the harness. It drives existing coding-agent CLIs —
`claude` (Anthropic), `gemini` (Google), `opencode` (open-source) — over
their structured protocols (stream-json and ACP), parses the event
streams, and re-renders them in a calm Textual TUI where many agents run
side by side. It adds a routing + delegation plane on top: queues,
workflows, an MCP server every spawned agent talks to, and an optional
Telegram front-end.

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

## Install

```bash
pip install aegis-harness        # or: uv pip install aegis-harness
```

Requires Python 3.13+ and at least one of: `claude`, `gemini`, or
`opencode` on your `PATH`, signed-in.

## Quickstart

```bash
aegis init     # interactive wizard — detects installed CLIs, writes .aegis.py
aegis          # full-screen TUI
```

The wizard finds whichever agent CLIs you have installed and walks you
through picking a model, permission mode, and optional queues. The
generated `.aegis.py` is plain Python — edit it freely afterwards.

## What you get

- **Multi-provider parity** — Claude Code, Gemini, and OpenCode all
  speak through aegis with the same UX (multi-turn, streaming,
  cancellation, per-session MCP injection). Gemini and OpenCode use
  [ACP](https://github.com/zed-industries/agent-client-protocol);
  Claude uses its stream-json bidirectional protocol.
- **Multi-tab TUI** — N independent agent sessions in one terminal.
  Per-tab profiles, generated alliterating handles
  (`lucid-knuth`, `wry-hopper`), per-block copy-to-clipboard, an inline
  spinner + rotating verb + timer while an agent works, cross-tab
  signalling (state dot + sticky `*` + bell when a backgrounded agent
  finishes).
- **Honest metrics** — true input (incl. cache) with cached %, output,
  tool calls, per-turn and per-session timing. Provisional while
  streaming, exact at turn end.
- **Queues + workflows** — first-class inter-agent delegation. Configure
  queues in `.aegis.py`; any agent can call `aegis_enqueue(queue,
  payload)` and get an automatic inbox callback when the worker
  finishes. Write Python workflows that orchestrate multiple agents
  (delegate / send / drain / spawn / close / bash) and run them via
  `aegis workflow run`.
- **Queue dashboard.** Always-on one-line strip above every
  conversation's status bar shows live per-queue depth and the most
  recent in-flight worker; `Ctrl+D` expands into a full-screen modal
  with `QUEUES / IN-FLIGHT / QUEUED / RECENT` bands and a detail
  panel that tails live assistant text. `>` jumps to the worker's
  tab. Every incoming handoff, queue callback, or Telegram message
  also mounts a distinct `✉` block in the receiving agent's
  transcript with a body preview, so you see what the agent is about
  to react to.
- **MCP plane** — every spawned agent gets injected with an aegis MCP
  server that exposes orientation (`aegis_meta`), session listing
  (`aegis_list_sessions`, `aegis_list_agents`), peer handoff
  (`aegis_handoff`), and queue dispatch (`aegis_enqueue`,
  `aegis_task_status`). No log scraping anywhere in the stack.
- **Headless + Telegram** — `aegis serve` runs the SessionManager and
  MCP plane without a TUI, with an optional Telegram front-end so you
  can drive agents from your phone.

## Keys

| Key | Action |
|---|---|
| `Enter` | Send |
| `Ctrl+T` / `Ctrl+N` | New tab (default agent) / new tab (pick agent) |
| `Ctrl+W` | Close tab (last → quit) |
| `Ctrl+1`..`9` / `Ctrl+Tab` / `Ctrl+←→` | Switch tabs |
| `Ctrl+D` | Open / close the queue dashboard |
| `Escape` | Interrupt the active turn (or dismiss the dashboard / agent picker) |
| `Click on a block` | Copy that message / tool result to clipboard |
| `Ctrl+Q` | Quit |

A backgrounded tab that finishes shows a `*` and rings the bell.

## Configuration

`.aegis.py` is plain Python. The wizard writes one for you; here's the
shape:

```python
from aegis import Agent, ClaudeCode, GeminiCLI, OpenCode

agents = {
    "default": Agent(provider=ClaudeCode(model="opus", effort="high",
                                          permission="auto")),
    "fast":    Agent(provider=GeminiCLI(model="gemini-3-flash-preview",
                                         permission="full")),
    "oss":     Agent(provider=OpenCode(model="opencode/kimi-k2.6",
                                        permission="full")),
}
default_agent = "default"

queues = {
    "review": {"agent": "fast", "max_parallel": 2},
}
```

Full reference: [Configuration](https://apiad.github.io/aegis/configuration/).

## Headless + Telegram

`aegis serve` runs the SessionManager and MCP plane without the TUI; add
a Telegram token to drive it from your phone:

```python
# .aegis.py
telegram_token = "…"        # or set AEGIS_TELEGRAM_TOKEN
telegram_chat_id = 123456   # the single allowed chat
```

Routing inside the chat:

- `/new [agent]` — spawn a new session
- `/close [handle]` — close a session
- `/interrupt` — interrupt the active turn
- `/<handle> text…` — one-shot to a specific session
- bare text — sent to the active session

A systemd unit template lives at `scripts/aegis-serve.service`.

## Docs

Full documentation: **[https://apiad.github.io/aegis/](https://apiad.github.io/aegis/)**

- [Install](https://apiad.github.io/aegis/install/)
- [Usage](https://apiad.github.io/aegis/usage/)
- [Configuration](https://apiad.github.io/aegis/configuration/)
- [Drivers](https://apiad.github.io/aegis/drivers/) — Claude / Gemini / OpenCode
- [Queues](https://apiad.github.io/aegis/queues/) — inter-agent delegation
- [Workflows](https://apiad.github.io/aegis/workflows/) — Python orchestration
- [MCP plane](https://apiad.github.io/aegis/mcp/) — the tool surface
- [Architecture](https://apiad.github.io/aegis/architecture/)
- [API reference](https://apiad.github.io/aegis/api/)

## Status

Beta. Personal-infrastructure-grade, evolves fast. Expect change before
1.0. See the [roadmap](https://apiad.github.io/aegis/roadmap/) for
what's next.

## License

MIT — see [LICENSE](LICENSE).
