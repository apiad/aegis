# Aegis Tasks

## Next up

- [ ] **Agent Groups** — two-level hierarchy (standalone agents + named groups). Broadcast, wait_all, wait_any. TUI sub-tabs + Ctrl+Shift+T + Ctrl+G. MCP + Workflow surface. Design doc: [[2026-05-25-aegis-agent-groups-design]].
  - Layer 0: `aegis_spawn` MCP tool (single agent spawn from MCP — prerequisite)
  - Layer 1: `AgentGroup` data model + `SessionManager` group methods
  - Layer 2: MCP tools (spawn_group, broadcast, wait_all, wait_any, list_groups)
  - Layer 3: Workflow engine methods (spawn_group, broadcast, wait_all, wait_any)
  - Layer 4: Workspace persistence for groups
  - Layer 5: TUI (group tab, sub-tabs, Ctrl+↑↓, Ctrl+Shift+T, Ctrl+G modal)

## Backlog

<!-- add items here -->
