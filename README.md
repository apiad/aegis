# Aegis

Meta-harness for coding agents. Phase 1: an interactive `aegis` CLI that drives
Claude Code via its `stream-json` protocol and re-renders output cleanly.

## Quick start

    uv pip install -e .
    aegis init          # writes .aegis.py
    aegis               # full-screen TUI with the default agent
    aegis --agent fast  # pick a named agent profile

Type in the input box and press Enter. Each tab is an independent agent with
a generated handle (e.g. `lucid-knuth`); its dot is green (idle), orange
(working), or red (error).

Keys: `Ctrl+T` new tab · `Ctrl+N` new tab (pick a profile) · `Ctrl+W` close
tab (closing the last quits) · `Ctrl+1`…`Ctrl+9` jump to tab · `Ctrl+Tab` /
`Ctrl+→` next · `Ctrl+←` prev · `Escape` interrupt the active turn · `Ctrl+Q`
quit. A background tab that finishes shows a `*` and rings the bell until you
switch to it.

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

Phase 1 + the Phase-1.5 full-screen Textual TUI. Specs in
`docs/superpowers/specs/`. The earlier workflow-engine prototype is preserved,
unbuilt, under `legacy/`.
