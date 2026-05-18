# Aegis

Meta-harness for coding agents. Phase 1: an interactive `aegis` CLI that drives
Claude Code via its `stream-json` protocol and re-renders output cleanly.

## Quick start

    uv pip install -e .
    aegis init          # writes .aegis.py
    aegis               # interactive session with the default agent
    aegis --agent fast  # pick a named agent profile

Type your first message at the `aegis>` prompt. `exit` / `quit` / Ctrl-D ends
the session.

## Config (.aegis.py)

Config is always Python. `aegis init` scaffolds an `agents` dict of
`Agent(harness, model, effort, permission)` plus `default_agent`:

```python
from aegis import Agent

agents = {
    "default": Agent(
        harness="claude-code",
        model="opus",
        effort="high",       # low | medium | high | max
        permission="auto",   # read | write | full | auto
    ),
}
default_agent = "default"
```

Permission levels: `read` (no mutations / plan mode), `write` (edits, no
shell), `full` (edits + shell), `auto` (harness-native smart mode).

With no `.aegis.py` in the current dir or `~`, `aegis` refuses to run and
points you at `aegis init`.

## Status

Phase 1 of the vision in `docs/superpowers/specs/`. The earlier
workflow-engine prototype is preserved, unbuilt, under `legacy/`.
