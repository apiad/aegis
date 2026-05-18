# Changelog

All notable changes to Aegis are documented here.
The format follows Keep a Changelog; this project uses SemVer (0.x).

## [0.1.0] - 2026-05-18

First tagged release — a usable, personal-infrastructure-grade meta-harness.

### Added
- CLI driver: runs Claude Code via `claude -p` stream-json (bidirectional,
  no log scraping); agent profiles from a Python `.aegis.py`.
- Full-screen Textual TUI replacing the line REPL.
- Multi-tab: N independent agent sessions, a sideways-scrolling tab bar,
  per-tab agent profiles, an `AgentPicker` modal, generated handles
  (`adjective-laureate`), cross-tab signalling (state dot + sticky `*` +
  bell).
- Theme engine (Textual-native) with the default **Ink** theme; themes are
  drop-in.
- Live status-line metrics: true input (incl. cache) with cached %, output,
  tool calls, turn / session time; provisional while streaming, exact at
  turn end.
- Lazy session start (harness spawns on first message, not tab open).
- `aegis --version`.

### Notes
- Not general-public-ready; runs from source via `uv`, drives a local
  `claude` CLI. The earlier FastMCP workflow-engine prototype is preserved
  under `legacy/`, unbuilt.
