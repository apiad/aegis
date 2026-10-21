- **The main agent hears when a person's gate opens and when it is passed.**
  It gets a `gate` message when the card opens and a `passed` one when the
  person continues, with nothing to answer, so it can tell the person their
  card waits without polling `workflow_status` from a monitor.

help: workflows#while-it-runs
