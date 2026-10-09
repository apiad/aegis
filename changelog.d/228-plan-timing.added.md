- **The plan says how long each item took and how much work is left.** A
  Fleet card shows a segmented bar, the count and an estimate of the work
  left (`▰▰▰▰▱▱▱▱ 4/8 · ~12m`), and the current item's time on its `now`
  row. The sidebar adds the plan's work and idle time and each item's time,
  and the spinner on the item in progress turns only while the agent works.
  Waiting on a monitor or a queue task counts as work; waiting on you is idle
  and never enters the estimate. Everything comes from timestamps aegis
  already stored; the agent reports nothing new.
