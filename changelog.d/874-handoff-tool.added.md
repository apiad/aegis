- **An agent can hand off its own session.** `session_handoff` closes the session to messages at once, and starts a new one when the agent's turn ends, on the same agent or another, with `msg` as what to do next.
help: handoffs#continue-in-a-fresh-session
