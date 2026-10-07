- **The new tab is a composer: type the first message, pick the agent and its
  settings, press Enter.** An agent is now a preset of harness, model, effort,
  permission and an optional `priming:` prompt, and every field but the priming
  can be changed per session. Agents get the same power as
  `mcp__aegis__session_spawn` and `mcp__aegis__agents_list`. `.aegis.yaml` has
  no defaults any more: an agent missing a field, or a queue missing
  `max_parallel`, is shown with what is missing instead of being filled in.
