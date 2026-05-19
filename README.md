# Aegis

> A meta-harness for coding agents — drives Claude Code in a multi-agent
> terminal UI.

[![CI](https://github.com/apiad/aegis/actions/workflows/ci.yml/badge.svg)](https://github.com/apiad/aegis/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue)](https://apiad.github.io/aegis/)
[![Python](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Aegis sits **above** the harness. It drives `claude -p` over its
`stream-json` protocol (no log scraping), parses the event stream, and
re-renders it in a calm full-screen TUI where many agents run side by side.

```
┌ aegis ───────────────────────────────────────────────┐
│ ● 1 lucid-knuth ·opus·   ● 2 wry-hopper ·fast· *      │
│                                                       │
│ › explain the retry logic                             │
│                                                       │
│ ✻ Thinking…                                           │
│ ⏺ Read(worker.py)                                     │
│   └ ok                                                 │
│ The retry path lives in _run_turn …                   │
│                                                       │
│ lucid-knuth ·opus· opus·full   ↑128k (94% cached) ↓1k │
│ ───────────────────────────────────────────────────── │
│ › ask something…                                      │
└───────────────────────────────────────────────────────┘
```

## Quickstart

```bash
uv pip install -e .
aegis init      # writes .aegis.py
aegis           # full-screen TUI
```

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), and a working
`claude` CLI on PATH.

## Keys

| Key | Action |
|---|---|
| `Enter` | Send |
| `Ctrl+T` / `Ctrl+N` | New tab / new tab (pick agent) |
| `Ctrl+W` | Close tab (last → quit) |
| `Ctrl+1`..`9` / `Ctrl+Tab` / `Ctrl+←→` | Switch tabs |
| `Escape` | Interrupt the active turn |
| `Ctrl+Q` | Quit |

Each tab is an independent agent with a generated handle; a backgrounded
tab that finishes shows a `*` and rings the bell.

## Configuration

`.aegis.py` is Python:

```python
from aegis import Agent

agents = {
    "default": Agent(harness="claude-code", model="opus",
                      effort="high", permission="auto"),
}
default_agent = "default"
```

`permission`: `read` | `write` | `full` | `auto`.

## Headless / Telegram

`aegis serve` runs the SessionManager headlessly, exposing the MCP plane
and (when configured) a Telegram bot front-end:

```python
# .aegis.py
telegram_token = "…"        # or set AEGIS_TELEGRAM_TOKEN
telegram_chat_id = 123456   # the single allowed chat
# auto_add_to_telegram_prompt = ""   # to disable the default brevity hint
```

Routing inside the chat:

- `/new [agent]` — spawn a new session (defaults to `default_agent`)
- `/close [handle]` — close a session (default: active one)
- `/interrupt` — interrupt the active turn
- `/<handle> text…` — one-shot to a specific session (doesn't move sticky)
- bare text — sent to the active session, with `auto_add_to_telegram_prompt`
  appended

A systemd unit template lives at `scripts/aegis-serve.service`.

## Docs & status

Full docs: **https://apiad.github.io/aegis/**

Phase 1 (CLI) → 1.5 (TUI + metrics) → 2 (multi-tab) → theming/Ink shipped.
Next: the MCP plane. Personal-infrastructure-grade, not general-public-ready;
the original FastMCP prototype is preserved (unbuilt) under `legacy/`.

## License

MIT — see [LICENSE](LICENSE).
