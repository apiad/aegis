# Aegis

A workflow orchestration server built on FastMCP (Fast Model Context Protocol) that enables multi-step workflows with human-in-the-loop interaction.

## Overview

Aegis provides a framework for defining and executing multi-step workflows. It uses async queues to yield instructions to users and wait for responses, making it ideal for interactive AI-assisted workflows.

## Features

- **WorkflowContext.step()** - Yields instructions and waits for user response via `workflow_step()`
- **@server.workflow()** decorator - Defines async generator workflows
- **@server.prompt()** - Creates MCP prompts for workflow initiation
- **State management** - UUID-based tracking across workflow steps

## Project Structure

```
aegis/
├── src/aegis/
│   ├── __init__.py     # Exports AegisServer, WorkflowContext
│   ├── server.py       # Core server implementation
│   └── demo.py         # Example usage
├── pyproject.toml      # Project configuration
└── README.md           # This file
```

## Installation

```bash
pip install -e .
```

## Running

```bash
aegis
```

This runs the demo server on `127.0.0.1:4243`.

## Example Usage

```python
from aegis import AegisServer, WorkflowContext

server = AegisServer()

@server.workflow()
async def my_workflow(ctx: WorkflowContext):
    """Describe your workflow."""
    await ctx.step("First instruction...")
    await ctx.step("Second instruction...")
    await ctx.step("Final step...")

server.run(transport="http", host="127.0.0.1", port=4243)
```

## Status

Version 0.1.0 - Early development stage with a working prototype demonstrating workflow orchestration.