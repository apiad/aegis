# API reference

Auto-generated from the source via
[mkdocstrings](https://mkdocstrings.github.io/). Only the public
surface — `from aegis import …` — is documented here. Internal
modules (drivers, MCP server internals, TUI widgets) are intentionally
not exposed.

## Configuration

::: aegis.config.Agent
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.ClaudeCode
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.GeminiCLI
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.OpenCode
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.Permission
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.Effort
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.ConfigError
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.find_project_root
    options:
      show_root_heading: true
      show_source: false

::: aegis.config.load_config
    options:
      show_root_heading: true
      show_source: false

## Queues

::: aegis.queue.Queue
    options:
      show_root_heading: true
      show_source: false

## Workflows

::: aegis.workflow.workflow
    options:
      show_root_heading: true
      show_source: false

::: aegis.workflow.WorkflowEngine
    options:
      show_root_heading: true
      show_source: false
      members:
        - spawn
        - send
        - drain
        - delegate
        - close
        - bash
        - log
        - list_sessions
        - list_agents
        - caller_handle

::: aegis.workflow.WorkflowError
    options:
      show_root_heading: true
      show_source: false

::: aegis.workflow.list_workflows
    options:
      show_root_heading: true
      show_source: false

::: aegis.workflow.get_workflow
    options:
      show_root_heading: true
      show_source: false
