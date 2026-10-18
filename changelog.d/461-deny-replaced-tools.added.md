- **Claude Code sessions start without the Claude Code tools aegis replaces.**
  Agent, SendMessage, Workflow, the cron and wakeup tools, Monitor, TaskStop,
  PushNotification and the task-list tools are off in every session with
  aegis's tools, which saves about 5.9k input tokens per session and keeps an
  agent on queues, monitors and plan_update. An agent that needs one keeps it
  with `keep_harness_tools:` in `.aegis.yaml`.

help: config#claude-code-tools-aegis-replaces
