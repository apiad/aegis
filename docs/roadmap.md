# Roadmap

## Shipped

### v0.1.0
- **Phase 1** — CLI driving Claude Code via stream-json.
- **Phase 1.5** — full-screen Textual TUI + live metrics.
- **Phase 2** — multi-tab + cross-tab signalling.
- **Polish** — generated handles, theme engine + Ink, lazy start,
  sideways tab scroll, honest cache-aware token metrics.

### v0.2.0
- **Phase 3 (slice 1)** — MCP plane foundation: shared FastMCP HTTP
  server owned by aegis; spawned agents injected strict + primed.
- **Phase 3 (slice 2)** — inter-agent tools: `aegis_list_sessions`,
  `aegis_list_agents`, `aegis_handoff`; per-pane self-reported handle.
- **Headless** — `aegis serve` + Telegram bridge.
- **Task queue v1** — `aegis_enqueue` + `aegis_task_status` MCP tools,
  `QueueManager` (FIFO + max-parallel cap + substrate-deterministic
  dispatch + JSONL replay), `InboxRouter` (per-handle delivery with
  universal sender tagging), `aegis_handoff` refactored through the
  same inbox channel.
- **Workflow scaffold v1** — `@workflow` decorator + auto-registry,
  `WorkflowEngine` runtime (delegate / send / drain / spawn / close /
  bash / log), `runner.run_workflow` with auto-drain + auto-close,
  `aegis workflow list/run` CLI, `aegis_run_workflow` MCP tool.

### v0.3.0 (current)
- **Multi-provider parity via ACP** — Gemini and OpenCode drivers
  rewritten on the official [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol)
  Python SDK. Multi-turn, streaming, cancellation, and per-session
  MCP injection now identical across all three providers.
- **TUI polish** — per-block click-to-copy with hover tooltip,
  inline `WorkingIndicator` (spinner + rotating verb + elapsed
  timer) mounted inside the transcript, glued ToolUse↔ToolResult
  blocks, max-variety alliterating handle generation.
- **Rich `aegis init` wizard** — detects installed CLIs, walks
  through agent + queue setup, refuses to clobber upstream
  `.aegis.py` without `--force`.
- **First PyPI release** — distributed as `aegis-harness`.

## Next

- **Multi-host distribution** — laptop ↔ VPS session sharing.
- **More drivers** — Codex, Aider, Cursor if/when they speak ACP.
- **Richer workflow primitives** — checkpoints, durable resume,
  parallel branches.
- **Spec language** — first-class plan files that workflows execute
  step by step.

Aegis is personal-infrastructure-grade and evolves fast; expect
change before 1.0.
