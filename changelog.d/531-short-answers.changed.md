- **`workflow_answer` and `workflow_stop` reply in a few lines.** They return
  the run's status, the named step's state and what the run still waits on,
  instead of the whole run, which was 60 KB on a long run and spilled past the
  tool output limit. `workflow_status` still returns the whole run.

help: workflows#when-a-step-fails
