---
title: Aegis Project State
date: 2026-04-09
tags: ["aegis", "project-state", "development"]
related: ["aegis-codebase", "aegis-onboard-2026"]
---

# Aegis Project State

## Current Status (April 2026)

- **Version**: 0.1.0 - Early development stage
- **Branch**: main (5 commits ahead of origin)

## Modified Files (Uncommitted)

- Makefile
- src/aegis/__init__.py
- src/aegis/demo.py
- src/aegis/server.py
- tests/conftest.py
- tests/test_integration.py
- tests/test_step.py

## Untracked Files

- notes/aegis-onboard-2026.md
- tests/test_demo.py

## Recent Commits

- Add comprehensive unit and integration tests
- Add global exception handling wrapper to workflows
- Add ctx.attempt() for retry on specific exceptions
- Enhance demo with note creation workflow and add sample notes
- Add automatic retry logic to workflow steps
- Add comprehensive README and improve workflow prompt descriptions
- Enhance onboarding workflow and improve logging for better debugging
- Update project scripts and add onboarding workflow for improved user interaction

## Project Description

Aegis is a workflow orchestration server built on FastMCP (Fast Model Context Protocol) that enables multi-step workflows with human-in-the-loop interaction via async queues.

## Key Features

- WorkflowContext.step() - Yields instructions and waits for user response
- @server.workflow() decorator - Defines async generator workflows
- @server.prompt() - Creates MCP prompts for workflow initiation
- Attempt class - Async context manager + iterator for retry on specific exceptions
- UUID-based state tracking across workflow steps

## Running

```bash
aegis
```
Runs demo server on 127.0.0.1:4243