# Configuration

Aegis is configured by a single file: `.aegis.py`. It is **plain
Python**, executed once at startup. Two names are required: `agents`
(a dict of profile name → `Agent`) and `default_agent` (which key in
that dict to use when no `--agent` is specified). Queues, Telegram, and
workflows are optional.

`aegis init` generates a starter file. The rest of this page is the
reference.

## Search order

1. Closest ancestor of the current directory containing `.aegis.py`.
2. `~/.aegis.py`.

With no `.aegis.py` anywhere, `aegis` refuses to start.

## Agents

```python
from aegis import Agent, ClaudeCode, GeminiCLI, OpenCode

agents = {
    "default": Agent(provider=ClaudeCode(model="opus", effort="high",
                                          permission="auto")),
    "fast":    Agent(provider=GeminiCLI(model="gemini-3-flash-preview",
                                         permission="full")),
    "oss":     Agent(provider=OpenCode(model="opencode/kimi-k2.6",
                                        permission="full")),
}
default_agent = "default"
```

### Provider classes

Each provider has its own config class so per-provider fields are
validated up-front.

| Provider | Class | Fields | Notes |
|---|---|---|---|
| Claude Code | `ClaudeCode` | `model`, `effort`, `permission` | The only provider with an `effort` knob. |
| Gemini CLI  | `GeminiCLI`  | `model`, `permission` | Permission maps to `--approval-mode`. |
| OpenCode    | `OpenCode`   | `model`, `permission` | Model strings use `provider/model` form. |

See [Drivers](drivers.md) for what each provider's `model` strings
look like and how permission maps to the underlying CLI's flag.

### Permission

`Permission` is a string enum: `"read"`, `"write"`, `"full"`, `"auto"`.

| Value | Claude | Gemini | OpenCode |
|---|---|---|---|
| `read`  | plan-mode | `--approval-mode plan`      | read-only tools |
| `write` | edit-mode | `--approval-mode auto_edit` | edit tools |
| `full`  | bypass    | `--approval-mode yolo`      | unrestricted |
| `auto`  | default   | `--approval-mode default`   | default |

### Effort (Claude only)

`Effort` is a string enum: `"low"`, `"medium"`, `"high"`, `"max"`.
Other providers don't expose an equivalent knob and ignore it.

### Legacy flat shape

The old flat keyword shape still works for back-compat:

```python
Agent(harness="claude-code", model="opus", effort="high",
      permission="auto")
```

This is equivalent to `Agent(provider=ClaudeCode(...))`. Prefer the
provider-object shape — it has stricter validation.

## Queues

Optional. Static configuration for the queue substrate; see
[Queues](queues.md) for the runtime model.

```python
queues = {
    "review":   {"agent": "fast", "max_parallel": 2},
    "research": {"agent": "default", "max_parallel": 1},
}
```

Each queue binds to one agent profile and a `max_parallel` cap. An
agent can then call `aegis_enqueue(queue="review", payload=...)` and
the substrate spawns a worker of that profile to run the payload.
Validation is fail-loud at boot: unknown agent refs or non-positive
caps cause `aegis` to abort with a clear error.

## Headless / Telegram

```python
# .aegis.py
telegram_token = "…"                  # or set AEGIS_TELEGRAM_TOKEN
telegram_chat_id = 123456             # the single allowed chat
# auto_add_to_telegram_prompt = ""    # set "" to disable the default brevity hint
```

Run with:

```bash
aegis serve
```

Routing inside the chat:

| Command | Action |
|---|---|
| `/new [agent]` | Spawn a new session (defaults to `default_agent`) |
| `/close [handle]` | Close a session (default: the active one) |
| `/interrupt` | Interrupt the active turn |
| `/<handle> text…` | One-shot to a specific session (doesn't move the sticky pointer) |
| bare text | Sent to the active session, with `auto_add_to_telegram_prompt` appended |

A systemd unit template lives at `scripts/aegis-serve.service`.

## Groups

Optional. Declarative shapes for agent committees. Inline form:

```yaml
groups:
  defaults:
    broadcast_timeout: 300
    default_reducer: join_by_handle
  presets:
    code_audit:
      profiles: [sec, style, logic]
```

Per-preset overlays live at `.aegis/groups/<name>.yaml` (file body is
the preset body — `profiles: [...]` directly). Inline + overlay
collisions on a preset name are fail-loud.

Presets become callable via the MCP plane:

```
aegis_group_spawn_mixed(group="rev", preset="code_audit")
```

See [Groups](groups.md) for the full surface.

## Workflows

To make Python workflows visible to `aegis workflow run` and to the
`aegis_run_workflow` MCP tool, **import** them in your `.aegis.py` so
the `@workflow` decorator registers them:

```python
from examples.tdd_step import tdd_step    # noqa: F401 — registers
```

See [Workflows](workflows.md) for writing your own.

## Remote plane

Optional. Lets this `aegis serve` enqueue work into another `aegis
serve` over HTTP and/or accept incoming enqueues from peers on the
same tailnet. Lives in `.aegis.yaml`, not `.aegis.py`.

**Outbound** — the list of remotes this serve can call:

```yaml
# .aegis.yaml
remotes:
  vps:
    url: http://100.64.0.5:8556
    token: "<optional bearer>"      # if the peer requires auth
    peer_name: zion                 # how the peer knows *us*
```

Per-remote overlay files at `.aegis/remotes/<name>.yaml` (body is the
remote body — `url:` directly). Name collisions between inline and
overlay are fail-loud.

`peer_name` (optional, v0.8.0+) is the name this caller goes by in
the *peer's* `remotes:` block. It's used as the `callback_to` value
when calling `aegis_enqueue(target="<peer>", callback=True)` — the
peer will then look that name up in its own outbound remotes to
route the callback back. Required for callback delivery; ignored
for fire-and-forget enqueues.

**Inbound** — opt-in section that turns on the receive side:

```yaml
remote_plane:
  bind: 100.64.0.5:8556         # tailnet IP, explicit
  accept_tokens: []             # optional bearer-token allowlist
  accept_from: []               # optional source-IP allowlist
```

Default off (key absent or empty block). Gates compose with AND — both
empty means "anything that reaches the port is trusted." See
[Remote plane](remote.md) for the full surface, error model, and
patterns.

**Symmetric deployment** — two hosts that each enqueue into the
other with callbacks both need to define each other in their
`remotes:` block. Each side's `peer_name` is *its own* name in the
other's eyes:

```yaml
# zion's .aegis.yaml
remotes:
  vps:
    url: http://100.64.0.5:8556
    peer_name: zion          # zion is "zion" to vps
remote_plane:
  bind: 100.64.0.4:8556
```

```yaml
# vps's .aegis.yaml
remotes:
  zion:
    url: http://100.64.0.4:8556
    peer_name: vps           # vps is "vps" to zion
remote_plane:
  bind: 100.64.0.5:8556
```

With this shape either side can call
`aegis_enqueue(target="<peer>", callback=True)` and the worker's
final message will flow back into the calling agent's inbox.

## Worked example

A full `.aegis.py` mixing everything:

```python
from aegis import Agent, ClaudeCode, GeminiCLI, OpenCode
from examples.tdd_step import tdd_step      # noqa: F401

agents = {
    "default": Agent(provider=ClaudeCode(
        model="opus", effort="high", permission="auto")),
    "worker-sonnet": Agent(provider=ClaudeCode(
        model="sonnet", effort="medium", permission="full")),
    "reviewer": Agent(provider=GeminiCLI(
        model="gemini-3.1-pro-preview", permission="auto")),
    "oss": Agent(provider=OpenCode(
        model="opencode/kimi-k2.6", permission="full")),
}
default_agent = "default"

queues = {
    "tdd":    {"agent": "worker-sonnet", "max_parallel": 2},
    "review": {"agent": "reviewer",      "max_parallel": 1},
}

telegram_token   = None    # set via AEGIS_TELEGRAM_TOKEN env var instead
telegram_chat_id = 123456789
```
