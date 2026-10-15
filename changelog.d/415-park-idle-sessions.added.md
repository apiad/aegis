- **Idle sessions can give their memory back.** A `park:` block names the harnesses whose sessions park, and after how many seconds. A session parks only when ending its harness kills nothing and nobody is looking at it; the next message, or the first keystroke in its composer, resumes it with its context. A Claude Code harness holds about 300 MB while it waits.

help: sessions#parked-sessions
