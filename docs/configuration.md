# Configuration

Aegis is configured by a single file: `.aegis.yaml`. It is **declarative
YAML**, parsed once at startup. Two sections are required: `agents:`
(profile name → agent spec) and `default_agent:` (which key in
`agents:` to use when no `--agent` is specified). Queues, Telegram,
schedules, remotes, groups, and workflow plugins are optional.

Two paths to author the file:

- **Interactive (TUI ConfigPanel).** Launch `aegis` in any directory.
  With no `.aegis.yaml` present, the TUI drops you straight into the
  ConfigPanel; press `a` to add an agent, save, and you're ready.
  Reach the same panel mid-session via `F2`.
- **Scriptable (CLI).** `aegis config agent add <slug> --provider …
  --model …` writes the same file. See [CLI surface](#cli-surface)
  below for the full set of verbs.

The rest of this page is the reference for what each section means.

## Search

`aegis` walks up from the current directory and uses the closest
ancestor containing a `.aegis.yaml`. With no `.aegis.yaml` anywhere,
`aegis` launches the TUI ConfigPanel so you can create one in place.

## Agents

```yaml
default_agent: default
agents:
  default:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
  fast:
    provider: gemini
    model: gemini-3-flash-preview
    permission: full
  oss:
    provider: opencode
    model: opencode/kimi-k2.6
    permission: full
```

### Providers

Each agent's `provider:` selects which CLI aegis drives.

| Provider value | Driver | Fields | Notes |
|---|---|---|---|
| `claude-code` | Claude Code  | `model`, `effort`, `permission` | The only provider with an `effort` knob. |
| `gemini`      | Gemini CLI   | `model`, `permission` | Permission maps to `--approval-mode`. |
| `opencode`    | OpenCode     | `model`, `permission` | Model strings use `provider/model` form. |

See [Drivers](drivers.md) for what each provider's `model` strings
look like and how permission maps to the underlying CLI's flag.

### Permission

`permission:` is one of `read`, `write`, `full`, `auto`.

| Value | Claude | Gemini | OpenCode |
|---|---|---|---|
| `read`  | plan-mode | `--approval-mode plan`      | read-only tools |
| `write` | edit-mode | `--approval-mode auto_edit` | edit tools |
| `full`  | bypass    | `--approval-mode yolo`      | unrestricted |
| `auto`  | default   | `--approval-mode default`   | default |

### Effort (Claude only)

`effort:` is one of `low`, `medium`, `high`, `max`. Other providers
ignore it.

## Drop-in overlays

Each top-level section also accepts overlay files under
`.aegis/{agents,queues,schedules,groups,remotes}/<name>.yaml`. The
file body **is** the entry (no extra `name:` wrapper); the filename
stem is the entry key.

```
.aegis/
  agents/
    sonnet.yaml         # body: provider:, model:, ...
  schedules/
    nightly.yaml        # body: workflow:, cron:, ...
```

Inline + overlay key collisions are fail-loud at boot.

## Queues

Optional. Static configuration for the queue substrate; see
[Queues](queues.md) for the runtime model.

```yaml
queues:
  review:
    agent: fast
    max_parallel: 2
  research:
    agent: default
    max_parallel: 1
```

Each queue binds to one agent profile and a `max_parallel` cap. An
agent can then call `aegis_enqueue(queue="review", payload=...)` and
the substrate spawns a worker of that profile to run the payload.
Validation is fail-loud at boot: unknown agent refs or non-positive
caps cause `aegis` to abort with a clear error.

### Budgets (optional)

Add a `budgets:` list to cap rolling USD spend or output-token volume
on a queue. All entries must allow for the enqueue to be admitted:

```yaml
queues:
  impl:
    agent: opus
    max_parallel: 2
    budgets:
      - usd: 1.00
        window: 1h
      - usd: 10.00
        window: 24h
      - output_tokens: 500000
        window: 1h
      - usd: 50.00
        window: 7d
  fast:
    agent: haiku-fast
    max_parallel: 4
    # no budgets: key → no caps
```

Each entry carries exactly one constraint (`usd` or `output_tokens`)
and a `window` string (`30m`, `1h`, `5h`, `24h`, `7d`, `1w`, `30d`).
When a queue exceeds any budget, new enqueues are rejected with a
structured error naming every blocking constraint and an `unblock_at`
ETA. See [Budgets](budget.md) for the full model, rejection shape, and
observability surface.

## Voice input (push-to-talk)

Optional, off by default. Install the extra: `pip install aegis-harness[voice]`
(base `harpio` + `sounddevice`; NOT `harpio[cli]`). `sounddevice` needs the
system PortAudio library — on Debian/Ubuntu: `sudo apt install libportaudio2`.

Enable per project in `.aegis.yaml`:

```yaml
voice:
  enabled: true
  model: base        # tiny | base | small | medium | large-v3
  key: ctrl+g        # Textual binding string
  preview: false     # true = live word-by-word (~2-4x cost, may lag on CPU)
  language: null     # e.g. "en", "es"; null autodetects
```

Press the key (default `ctrl+g`) to start dictating into the focused pane's
input; press again — from any tab — to stop. Text is never auto-sent: edit and
press Enter. One recording at a time, and it stays anchored to the input it
started on even if you switch tabs. Transcription is fully on-device (via
[harp](https://github.com/apiad/harp)). If the extra isn't installed, the key
shows an install hint instead of recording.

## Headless / Telegram

```yaml
telegram:
  token: "..."            # or set AEGIS_TELEGRAM_TOKEN (env wins)
  chat_id: 123456         # the single allowed chat
  # auto_prompt: ""       # set to "" to disable the default brevity hint
```

Run with:

```bash
aegis serve
```

See [Telegram](telegram.md) for the full command surface, setup,
output examples, `@<peer>` cross-host syntax, and FAQ.

A systemd unit template lives at `scripts/aegis-serve.service`.

## Groups

Optional. Declarative shapes for agent committees:

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

`@workflow`-decorated functions are auto-discovered. At boot, aegis
imports every `*.py` under each `plugin_dirs:` entry (default
`.aegis/plugins/`):

```yaml
plugin_dirs:
  - .aegis/plugins
  - my_workflows
```

Drop your workflow modules into any listed folder; the `@workflow`
decorator fires at import time and the name lands in the registry.
See [Workflows](workflows.md) for writing your own.

To enable one of aegis's built-in workflow modules (under
`aegis.workflows.builtins.*`), name it in `workflows:`:

```yaml
workflows:
  - my_builtin
```

## Remote plane

Optional. Lets this `aegis serve` enqueue work into another `aegis
serve` over HTTP and/or accept incoming enqueues from peers on the
same tailnet.

**Outbound** — the list of remotes this serve can call:

```yaml
remotes:
  vps:
    url: http://100.64.0.5:8556
    token: "<optional bearer>"      # if the peer requires auth
    peer_name: zion                 # how the peer knows *us*
```

Per-remote overlay files at `.aegis/remotes/<name>.yaml` (body is the
remote body — `url:` directly). Name collisions between inline and
overlay are fail-loud.

`peer_name` is the name this caller goes by in the *peer's*
`remotes:` block. It's used as the `callback_to` value when calling
`aegis_enqueue(target="<peer>", callback=True)` — the peer will then
look that name up in its own outbound remotes to route the callback
back. Required for callback delivery; ignored for fire-and-forget
enqueues.

**Inbound** — opt-in section that turns on the receive side:

```yaml
remote_plane:
  bind: 100.64.0.5:8556         # tailnet IP, explicit
  peer_name: zion               # this serve's own name (see below)
  accept_tokens: []             # optional bearer-token allowlist
  accept_from: []               # optional source-IP allowlist
```

`peer_name` is **this serve's identity** as seen by its peers. It
populates the `from_peer` field of outbound callback POSTs so the
receiver can match it against its own `remotes:` map. Required when
this serve also has `remotes:` configured (i.e. might send callbacks);
receiver-only deployments may leave it unset.

Convention: the `peer_name` here must equal the value you use in
every peer's `remotes.<this-serve>.peer_name` — it's the single
identity by which the rest of the tailnet knows you. If `remotes:` is
set but `remote_plane.peer_name` is not, the serve still boots but
the outbound callback observer is not installed; any
`aegis_enqueue(target=…, callback=True)` then returns a loud error
at call time. Fire-and-forget enqueues continue to work unchanged.

Default off (key absent or empty block). Gates compose with AND — both
empty means "anything that reaches the port is trusted." See
[Remote plane](remote.md) for the full surface, error model, and
patterns.

## CLI surface

Every section above is also reachable through `aegis config`:

```
aegis config show [--json]
aegis config agent list / add <slug> --provider --model [--effort] [--permission] / remove <slug>
aegis config queue list / add <name> --agent --max-parallel [--budget …]+ / remove <name>
aegis config telegram show / set [--token --chat-id --auto-prompt
                                  + matching --clear-* variants]
aegis config default-agent <slug>
aegis config plugin-dir list / add <path> / remove <path>
```

Budget spec: `<constraint>:<limit>:<window>`, e.g.
`usd:1.00:1h` or `output_tokens:500000:1h`. The `--budget` flag is
repeatable. Every writing verb validates against the YAML loader's
invariants before persisting; an invalid argument leaves the on-disk
file unchanged.

## Worked example

A full `.aegis.yaml` mixing everything:

```yaml
default_agent: default

agents:
  default:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
  worker-sonnet:
    provider: claude-code
    model: sonnet
    effort: medium
    permission: full
  reviewer:
    provider: gemini
    model: gemini-3.1-pro-preview
    permission: auto
  oss:
    provider: opencode
    model: opencode/kimi-k2.6
    permission: full

queues:
  tdd:
    agent: worker-sonnet
    max_parallel: 2
  review:
    agent: reviewer
    max_parallel: 1

telegram:
  # token resolved from AEGIS_TELEGRAM_TOKEN env var
  chat_id: 123456789

plugin_dirs:
  - .aegis/plugins
```
