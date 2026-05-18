# Configuration

Config lives in `.aegis.py` (current directory, then `~/.aegis.py`). It is
**Python**, executed to read two names: `agents` and `default_agent`.

```python
from aegis import Agent

agents = {
    "default": Agent(harness="claude-code", model="opus",
                     effort="high", permission="auto"),
    "fast":    Agent(harness="claude-code", model="sonnet",
                     effort="low", permission="read"),
}
default_agent = "default"
```

| Field | Values |
|---|---|
| `harness` | `claude-code` (only driver today) |
| `model` | passthrough alias (`opus`, `sonnet`, …) |
| `effort` | `low` \| `medium` \| `high` \| `max` |
| `permission` | `read` \| `write` \| `full` \| `auto` |

`aegis init` writes a starter `.aegis.py`. `Ctrl+N` in the TUI spawns a tab
for any configured profile.
