---
title: Aegis Onboard Exploration
date: 2026-04-09
tags: ["aegis", "codebase", "workflow", "exploration"]
related: ["aegis-codebase"]
---

# Aegis Onboard Exploration

# Aegis Onboard Exploration

Completed the onboard workflow to explore the Aegis codebase.

## Key Findings

- **Project**: Aegis - workflow orchestration server built on FastMCP
- **Purpose**: Enables multi-step workflows with human-in-the-loop interaction via async queues
- **Structure**:
  - `src/aegis/__init__.py` - Exports AegisServer, WorkflowContext
  - `src/aegis/server.py` - Core server implementation
  - `src/aegis/demo.py` - Example usage
  - `tests/` - Unit and integration tests

## Running

```bash
aegis
```
Runs demo server on `127.0.0.1:4243`

## Development Status

- Branch: main, 5 commits ahead of origin
- Recent features: `ctx.attempt()` for retry on specific exceptions
- Has comprehensive unit and integration tests

## Related Notes

- [[aegis-codebase]]

