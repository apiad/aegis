- **A `session: main` step no longer waits behind another one.** A main step
  was shown as queued while any other main step of its run was open, as if it
  waited for room, though the main agent holds no slot. A one-minute step on
  one branch could sit behind a review on another branch that waited 25
  minutes on a monitor. Each main step is now delivered when it is ready, and
  with several open, `workflow_result` and `workflow_output` take `node`, the
  step they report.
