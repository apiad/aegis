# Roadmap

Shipped (v0.1.0):

1. **Phase 1** — CLI driving Claude Code via stream-json.
2. **Phase 1.5** — full-screen Textual TUI + live metrics.
3. **Phase 2** — multi-tab + cross-tab signalling.
4. **Polish** — generated handles, theme engine + Ink, lazy start,
   sideways tab scroll, honest cache-aware token metrics.

Shipped (v0.2.0):

5. **Phase 3 (slice 1)** — MCP plane foundation: shared FastMCP HTTP
   server owned by aegis; spawned agents injected strict + primed,
   with an `aegis_meta` orientation tool.
6. **Phase 3 (slice 2)** — inter-agent tools: `aegis_list_sessions`,
   `aegis_list_agents`, `aegis_handoff` (fire-and-forget context
   transfer); per-pane self-reported handle baked into priming so each
   agent knows who it is.

Next:

- **Phase 4+** — task queues, distribution (laptop ↔ VPS), additional
  harness drivers.

Aegis is personal-infrastructure-grade and evolves fast; expect change
before a 1.0.
