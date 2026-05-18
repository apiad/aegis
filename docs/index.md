# Aegis

A meta-harness for coding agents. Aegis sits **above** the harness — it
drives `claude -p` over `stream-json`, parses the event stream, and
re-renders it in a calm full-screen TUI where many agents run side by side.

- **Multi-tab**: N independent agent sessions, per-tab profiles, generated
  handles, cross-tab signalling.
- **Themed**: Textual-native theme engine, calm **Ink** default.
- **Honest metrics**: true input (incl. cache) + cached %, output, tools,
  turn / session time.

See **[Install](install.md)** to get going, or the
**[Architecture & Vision](architecture.md)**.
