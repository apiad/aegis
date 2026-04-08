---
title: Aegis Codebase
date: 2026-04-08
tags: ["aegis", "codebase", "workflow", "fastmcp"]
related: []
---

# Aegis Codebase

# Aegis Codebase

Aegis is a workflow orchestration server built on FastMCP (Fast Model Context Protocol) that enables multi-step workflows with human-in-the-loop interaction.

## Project Structure

- `src/aegis/__init__.py` - Exports `AegisServer`, `WorkflowContext`
- `src/aegis/server.py` - Core implementation with workflow decorators and queue-based step management
- `src/aegis/demo.py` - Example usage

## Core Classes

- **AegisServer**: Main server class extending FastMCP
- **WorkflowContext**: Context for workflow execution with state management across steps

## Key Features

- `workflow()` decorator - Defines async generator workflows
- `WorkflowContext.step()` - Yields instructions and waits for user response
- `@server.prompt()` - Creates MCP prompts for workflow initiation
- UUID-based tracking across workflow steps
- Version 0.1.0 - early prototype stage

## Usage

```python
from aegis import AegisServer, WorkflowContext

server = AegisServer()

@server.workflow()
async def my_workflow(ctx: WorkflowContext):
    await ctx.step("First instruction...")
    await ctx.step("Second instruction...")

server.run(transport="http", host="127.0.0.1", port=4243)
```

            ## Related Notes

            No related notes.
