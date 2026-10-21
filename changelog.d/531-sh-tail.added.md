- **A failed or timed-out command keeps the end of its output.** The last 50
  lines show in the failure message and as the step's `tail` in
  `workflow_status`. Before, a step past its deadline said only that it ran
  past it, and a failed one only its last line, so each failure needed a
  rerun by hand to see why.

help: workflows#when-a-step-fails
