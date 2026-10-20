- **Agents are told what they track, and a stalled monitor wakes its owner.**
  A session's next message carries a short status block when its plan, its
  live monitors or the queue tasks it has out changed. A warning rides on the
  next message or aegis tool result when a plan has gone ten minutes of work
  untouched, or when a monitor has stalled, cannot run its check or has
  overrun its ETA; the monitor warnings wake an agent that was waiting
  instead of leaving it asleep until the timeout. Thresholds live under
  `notices:` in `.aegis.yaml`, declared by each source.

help: monitors#a-monitor-that-stalls
