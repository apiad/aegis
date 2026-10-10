- **Agents can close sessions with `session_close`.** Closing six finished
  workers used to take a hand-written websocket call with the server's token. A
  session the agent spawned, or a worker whose task it enqueued, now closes at
  once when it is done, into the archive like the Close button. Any other close,
  including the agent's own session, is refused first with what that session is
  doing, who started it and a suggestion to ask the person, and goes through on a
  second call with the one-time token the refusal carries (valid five minutes).
