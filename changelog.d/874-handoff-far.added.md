- **A handoff can continue on a linked server.** `/handoff --agent name@server` and `session_handoff` with `host` start the new session there, under the same policy as a spawn, and archive the old one once the far side has its first message.
help: handoffs#continue-in-a-fresh-session
