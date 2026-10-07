# aegis: the new-tab composer, and agents as presets

**Status: draft, 2026-10-07** (issue #155). Designed with Alex in a brainstorm with
mockups. No plan yet.

## What this delivers

Opening a tab becomes the same act as writing to an agent. The `#new` view is an
input box centred on the page, as in OpenCode, with a row of dropdown chips
under it: agent, harness, model, effort, permission. Alex types the first
message, adjusts any chip, and Enter spawns the session and sends the message in
one call.

An agent in `agents:` stops being the only thing that can be spawned. It is a
named preset: it fills the chips and carries a priming prompt, and every chip can
then be changed for this one session. Agents get the same power over MCP: a
session can spawn another with any combination, through
`mcp__aegis__session_spawn`.

Out of scope: extra MCP servers per spawn (#153), an agent closing a session it
spawned, and per-task overrides on `queue_enqueue`.

## Decisions

| Question | Decision | Why |
|---|---|---|
| What an agent is | A named preset of harness, model, effort, permission and priming | Alex wants to pick any combination; a preset is a starting point, not a gate |
| Which fields a spawn can override | Harness, model, effort, permission, cwd. Not the priming | The priming belongs to the agent's definition; one-off instructions go in the first message |
| Where the priming lives | `priming:` text inside the agent's entry in `.aegis.yaml` | One place to read an agent; Alex asked for it in the agent config |
| How the priming reaches the model | After aegis's primer, in the same `--append-system-prompt` | aegis's primer is what makes its tools usable; an agent adds to it, never removes it |
| A spawn with no agent | Uses `default_agent`. With neither, the spawn fails with `no_agent` | No hard-coded fallback: every session starts from a preset someone wrote down |
| Which priming a resumed session gets | The text recorded at spawn, not the current YAML | Claude Code does not keep the system prompt in its session file, so a resume passes it again; reading the YAML would change old sessions when an agent is edited |
| Agents spawning | `session.spawn` is marked for agents, with the same params | DESIGN.md: one registry, every caller. The vision spec allows an agent to spawn on its own server |
| A spawned session reporting back | Nothing new. The spawner uses `peer_read` or `peer_handoff` | Both exist; a callback is what queues are for |
| Naming | `profile` becomes `agent` on the wire: the `agent` param, `agents.list` | The config key is `agents:` and the screen says agent; the only caller of the old names is aegis's own client |

## Config

```yaml
agents:
  opus:
    provider: claude-code
    model: opus
    effort: high
    permission: full
  reviewer:
    provider: claude-code
    model: opus
    effort: max
    permission: read
    priming: |
      You review changes for correctness. Read the diff, run the tests that
      touch it, and report findings ranked by severity, with file and line.
default_agent: opus
```

`priming:` is optional; without it the session gets only aegis's primer. The
other fields and their accepted forms are unchanged (`profiles.py`): flat
`harness:`, `provider:` as a string, or a nested `provider:` mapping. A field an
agent omits keeps the loader's current default.

## Operations

### `agents.list` (replaces `profiles.list`; open to agents)

Returns what the chips need, computed in Python so the browser only draws it:

- `agents`: name, harness, model, effort, permission, `enabled` (its harness has
  a driver) and `has_priming`. The priming text itself does not cross the wire.
- `default`: `default_agent`, or null.
- `harnesses`: each known harness name with `supported`. Today `claude-code` is
  supported and `opencode` is listed but not.
- `models`: per harness, the suggestions for the model chip. For `claude-code`,
  Claude Code's aliases (`opus`, `sonnet`, `haiku`, `fable`) followed by every
  model an agent of that harness names, without duplicates.
- `cwd`: the default working directory, as today.

### `session.spawn` (open to agents)

| Param | Type | Meaning |
|---|---|---|
| `agent` | str, optional | The preset. Omitted means `default_agent` |
| `harness`, `model`, `effort`, `permission` | optional | Override the preset's value for this session |
| `cwd` | str, optional | As today, inside the config root. For an agent caller it defaults to the caller's cwd, as `queue_enqueue` does |
| `prompt` | str, optional | Sent as the first message right after the spawn |

Resolution, per field: the override if given, otherwise the agent's value. The
priming is always the agent's. Errors: `no_agent` (no `agent` and no
`default_agent`), `unknown_agent`, `harness_unsupported` (the resolved harness,
whether from the agent or an override), `bad_cwd`, `bad_config`,
`claude_not_found`.

With `prompt`, the operation spawns and then sends, and returns `log_id` and
`handle` as today. If the send fails the session still exists, and the error
names it so the caller can open it.

## What a session records

The `spawn` record in the store, and the meta built from it, gain:

- `agent`: the preset's name. Old records carry `profile`; the meta reader
  accepts either.
- `harness`.
- `priming`: the resolved text, or absent.
- `overridden`: the fields the spawn changed from the preset, such as
  `["model"]`.
- `spawned_by`: the caller's log id when an agent spawned it.

`SpawnSpec` carries harness and priming. `Host.spawn_args` appends the spec's
priming to aegis's primer, on the first start and on every resume. The session
card shows the agent as `opus*` when `overridden` is not empty.

## The new-tab view

```
 fleet   aegis-new-tab   + new
                     aegis
              ~/Workspace/repos/aegis ▾
   ┌──────────────────────────────────────────────────────┐
   │ Read issue #153 and propose where the MCP server     │
   │ list should come from.                               │
   │ [agent opus* ▾] [claude-code ▾] [sonnet ▾]           │
   │ [effort high ▾] [perm full ▾]  reset            [↵]  │
   └──────────────────────────────────────────────────────┘
   Enter spawns and sends · Shift+Enter new line
```

- **The agent chip** fills the other chips. Changing any other chip marks the
  agent `opus*`, paints the changed chip's value in the accent colour, and shows
  `reset`, which restores the preset's values.
- **The harness chip** lists every harness; unsupported ones are disabled, as an
  agent with an unsupported harness is today.
- **The model chip** offers the suggestions from `agents.list` for the selected
  harness and accepts any typed model id.
- **The working directory** is the line under the wordmark; a click makes it
  editable.
- **Enter** calls `session.spawn` with the agent, the changed fields only, the
  cwd and the text as `prompt`, then focuses the new tab. On an empty box it
  spawns an idle session. A failure shows its code and message under the box and
  keeps the text.
- **Each `+`** starts from the last agent spawned in this browser (local storage)
  with no overrides, or from `default_agent` when there is none.

## Tests

All of the repo's usual shape: the fake claude against a real `aegis serve`.

- Browser: spawn from the composer with the model overridden; the new tab opens,
  the first message arrives as the user entry, and the child's argv carries the
  overridden model. Change a chip and press `reset`; the preset's values return.
- MCP: the fake claude calls `session_spawn` with `agent` and an `effort`
  override; the new session's argv and its `spawned_by` are checked.
- Priming: an agent with `priming:` spawns with aegis's primer followed by the
  priming in `--append-system-prompt`. Edit the YAML, stop the session, send to
  it: the resumed process carries the old text.
- Resolution: `no_agent`, `unknown_agent`, and `harness_unsupported` from an
  override.
- Old stores: a meta with `profile` and no `agent` still boots into the archive
  with its agent name.
