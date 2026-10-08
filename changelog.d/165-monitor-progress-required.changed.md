- **`monitor_start` requires `progress`, which may be `null`.** 44 of 51
  monitors armed on zion in early October had no progress command, 33 of them
  waits on CI checks that could have been counted. Leaving the field out is now
  refused, and the field, the tool and the primer tell the agent how to measure
  progress: finished checks, jobs, files or lines over the total, or elapsed over
  expected time. A real Haiku asked to wait on a pull request's CI gave the
  monitor a progress command in 1 of 6 runs before and 8 of 8 after.
