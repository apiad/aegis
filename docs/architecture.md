# Architecture & Vision

Aegis is a **meta-harness**: it does not call model APIs: it drives an
existing coding-agent harness (Claude Code) as a subprocess and adds a
control plane above it.

## Layering

- **Driver** (`drivers/`) — spawns `claude -p` with
  `--input-format/--output-format stream-json`, sends user messages, yields
  typed events. The seam for future harnesses.
- **Events / render** — a typed parser (`events.py`) and a pure
  `render_event` mapping events → Rich renderables.
- **ConversationPane** — one live conversation: session, transcript,
  metrics, state.
- **AegisApp** — the shell: N panes in a `ContentSwitcher`, the tab bar,
  the agent picker, cross-tab signalling, the theme.

## Vision (abridged)

Aegis is the layer above the harness — multiplexing, routing, and mediating
between concrete agents. The roadmap runs from a single-tab CLI to a
multi-agent mesh; see **[Roadmap](roadmap.md)**. Full design history is kept
internally with the project.
