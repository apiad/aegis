- **A Codex child that dies under a send is reported once, by its exit.** When
  `codex app-server` exited before it answered the request a send was waiting
  on, the send also failed: the transcript gained a "the message did not reach
  the agent" error on top of the exit row, the session went idle before it went
  stopped, and whoever sent the message got an error. Now the exit row is the
  only report, as it already was for OpenCode.
