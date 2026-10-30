- **A spent loop no longer holds its session and deadlocks the run.** A `loop`
  with `at_max: done` whose rounds ran out without `until` passing kept showing
  `running`, so a session its steps reused stayed open and counted against
  `limits`. A later fresh step could then sit `queued` for good with no agent
  working. The loop now shows `done` and its session closes.
