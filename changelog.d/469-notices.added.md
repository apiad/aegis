- **Agents are told when the host runs short.** Each live session learns
  once, on its next message or aegis tool result, that the disk, memory, CPU
  or quota has crossed a line, and again when it clears. A notice never
  starts a turn and never enters the system prompt. Every agent frees only
  what its own session created; one session the person opened asks about
  the rest, and a notice at critical shows a toast. `/announce` and
  `notice_announce` let a person or an admin's agent tell every agent
  something the same way, and `notices:` in `.aegis.yaml` moves or turns off
  each line.

help: sessions#notices
