# aegis: quiet sessions, a tab hidden until its turn ends

**Status: designed, 2026-10-14** (issue #386). Designed in a brainstorm in a
session tab.

## What this delivers

A person running background agents (a secondary task another agent spawned, a
queue worker) no longer sees their tabs while they work. Such a session is
**quiet**: it has no tab, but it keeps its card in the Fleet, drawn dimmed with
a `zz` mark. It comes back to the tab bar on its own when its turn ends, and
stays there until someone quiets it again.

The person quiets a working or waiting session from the sidebar, next to Stop.
An agent quiets one with an MCP tool, or starts one quiet. Queue workers start
quiet by default.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Snooze or quiet | One state, `quiet`. "Snooze" is quieting a session that already exists | Both hide a tab until the session ends its turn; two names for one rule would drift apart |
| Where the state lives | On the server, in the session's meta | An agent must be able to quiet another session, so it cannot live in a browser's `localStorage`. It also survives a restart, and every browser shows the same tabs, as today |
| What can be quieted | A session whose attention is `working` or `waiting` | A stopped or idle session has no turn to end, so it would never come back on its own (decided in the brainstorm) |
| What brings it back | Its turn ends with attention `done`, `review`, `needs_you` or `error`, or the person clicks its Fleet card | A turn that ends to wait on a monitor, a queue task or a background Bash is `waiting`: the agent has not finished, so it stays quiet |
| What a queue worker needs to come back | `error` only | A worker's `needs_you` is already `done` (`attention.card`), and a completed worker is archived (`queues.py`, `_complete`). Surfacing on `done` would flash a tab that is archived a moment later |
| After it comes back | It stays visible until quieted again | Predictable; re-quieting is one click or one tool call |
| Who can un-quiet | Only the end of a turn and the person's click | Agents only quiet. Nothing asked for an agent to surface another one early |

## Server

**State.** `Session` gains `quiet: bool`, written into its meta like `title`
(`registry.rename` shows the pattern: set it, then `self.metas.write(s.meta())`).
`wire()` carries it to the browser and to `session_list`.

**Quieting.** A new op `session.quiet` takes a `log_id`. It refuses with
`OpError("not_running", ...)` when the session's attention is neither `working`
nor `waiting`, and refuses an archived session as `no_session`. Quieting a
session already quiet is a no-op that returns it.

**Un-quieting by the person.** A second op `session.unquiet` takes a `log_id`
and clears the flag. Only the browser calls it, so it is not an agent op.

**The MCP tool.** `session_quiet` takes a `handle` of a session on this server
(the caller's own handle included). It is the agent face of `session.quiet`
and refuses with the same errors.

**Spawn.** `SpawnParams` gains `quiet: bool = False`. A quiet spawn without a
`prompt` is refused: a session with no first message is idle, so nothing would
ever end its turn. `/spawn` in the composer gains `--quiet` (`commands.py`,
`parse_spawn` and the usage line).

**Queues.** A queue's config in `.aegis.yaml` gains `quiet`, default `true`
(`config.py`: `QUEUE_KEYS` and the queue dataclass, beside `max_parallel`).
`queues.py` passes it to the worker's spawn.

**Coming back.** `Registry.turn_ended` and `Registry.exited` already run at the
two moments a turn can end. Each one, for a quiet session, computes the card
(`_attention`) and clears `quiet` when the attention is one of `done`,
`review`, `needs_you`, `error`, or only `error` for a worker. Clearing writes
the meta and publishes, like any other change.

Archiving a session drops nothing special: a reopened session starts visible.

## Browser

**Tabs.** `render()` in `app.js` passes `renderTabs` only the sessions whose
meta is not `quiet`. `TabOrder.arrange` still gets every open session, so a
session that comes back keeps the place it had.

**When the open tab goes quiet.** If the session in view becomes quiet (the
person pressed the button, or an agent quieted it), the view moves to the
neighbouring tab, or to the Fleet when none is left.

**The sidebar button.** A `Quiet` button joins Stop, Restart and Close in
`.side-acts`, with a `zz` icon. It is enabled only while the session's
attention is `working` or `waiting`, and calls `session.quiet` through
`callFor`, like Stop.

**The Fleet.** A quiet card gets a `quiet` class: dimmed, with a `zz` mark
beside its attention mark. Clicking it calls `session.unquiet` and opens the
session, so it is back in the tab bar.

**Attention.** `byNeed` (Alt+J) and the ping that notifies the person skip
quiet sessions. In practice they rarely meet one, since the attentions they
react to are the ones that clear `quiet`.

## Linked servers

A far session carries its own server's `quiet` flag in its meta, so the tab bar
hides it the same way. The sidebar button and the Fleet click go through
`callFor`, which already routes Stop and Close to the session's own server.

## What is not built

- A keyboard shortcut and a `/quiet` composer command. The sidebar button and
  the MCP tool cover both ways in; a shortcut is cheap to add later.
- A checkbox in the new-session form.
- An agent tool to un-quiet.

## Tests

- `attention`/registry: a quiet session clears on each of `done`, `review`,
  `needs_you`, `error`; stays quiet on `waiting`; a quiet worker clears only on
  `error`; a worker exit clears it.
- `session.quiet` refuses an idle, a stopped and an archived session, and
  accepts a working and a waiting one.
- `session_spawn(quiet=true)` without a prompt is refused; with one, the
  session starts quiet. `/spawn --quiet` parses.
- A queue worker starts quiet; with `quiet: false` on its queue it does not.
- The flag survives a server restart (meta round trip).
- Browser: the tab list skips quiet sessions; a quiet card carries the class;
  clicking it un-quiets; quieting the open tab moves the view.
- By hand, in a browser: quiet a working session from the sidebar, watch the
  tab go, watch it come back when the turn ends.
