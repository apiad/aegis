- **Quiet sessions keep background work out of the tab bar.** A session that is working or waiting can be quieted from the session panel, by an agent with `session_quiet`, or from the start with `session_spawn(quiet=true)` and `/spawn --quiet`. It loses its tab, keeps a dimmed card in the last group of the Fleet, and gets its tab back when its turn ends or someone clicks its card. Queue workers start quiet, so only a failed one shows.

help: sessions#quiet-sessions
