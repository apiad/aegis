- **Schedules fire a prompt on a clock.** A schedule sends its prompt every
  few minutes, on a cron line or once at a set time, into a new session from
  an agent or into an open session's inbox. An optional bash check fires a
  tick only when its output changed, and hands that output to the agent. The
  overlap rule waits for a run still working, skips the tick or runs beside
  it, and a new run is archived when its last `turn_end` said done. Agents get
  nine `schedule_*` tools (#390).
help: schedules
