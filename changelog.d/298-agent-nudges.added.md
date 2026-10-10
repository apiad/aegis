- **aegis nudges an agent that drifts.** An agent whose last turn ended with
  no `turn_end` and that has sat 10 minutes with nothing to wait on is asked
  once what it is doing: done, needs you, or something else. An agent that has
  worked 15 minutes without touching a plan that still has items not done is
  told, once per plan, to update it or carry on. Both arrive as inbox rows
  headed `aegis:nudge`, never as your message. A question to you, a wait on a
  monitor or task, an interrupt and queue workers are left alone. Of 232 turns
  in the vps stores, 151 ended with no `turn_end`, and the card read each as
  done.
