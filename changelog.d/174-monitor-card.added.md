- **A monitor has an ETA again, and a card that shows everything about it.**
  Hovering a monitor in the session sidebar opens a card with its description,
  id, start, check interval and timeout, a chart of its progress readings with
  the ETA, and each of its `done`, `progress` and `fail` commands with the last
  result: exit code, verdict, and its last stderr line. The row shows the ETA
  (`66% · ~2m`) or how late it is. The ETA takes the rate since progress first
  moved, so a CI wait's setup time at 0 no longer stretches it. A check bash
  cannot run (a missing command, including one inside a pipeline) marks the row
  `check fails` in red: before, it looked exactly like a condition still
  waiting until its timeout an hour later.
