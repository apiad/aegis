- **A running workflow's main agent shows waiting, not a stale needs_you.**
  Approving a run left the main agent's card asking for that approval until
  the run first woke it, sometimes for hours, and between agent steps the
  card did not count the run at all, so the agent was nudged for being idle
  while it waited correctly. Now the approval answers the ask, the card says
  waiting on `workflow <run>` for every kind of step and between them, and
  the main agent gets no idle nudge while its run lives.
