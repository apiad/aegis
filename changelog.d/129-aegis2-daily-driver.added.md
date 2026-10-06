- **aegis2 agents get aegis's tools over MCP: monitors, queues, handoffs.** Each
  session's `claude` connects to the server's `/mcp` with its own token, so no
  tool takes a handle. Tools are named after their operation
  (`mcp__aegis__monitor_start`, not `mcp__aegis__aegis_monitor`). Monitors poll
  bash conditions and wake their owner through an inbox that holds messages until
  a turn ends; queues from `.aegis.yaml` spawn worker sessions and call back with
  the result once the worker has nothing left pending. Monitors, held messages and
  running tasks survive a restart. Still experimental.
