---
title: Aegis Telegram — Substrate Command Surface (v0.10)
date: 2026-05-26
status: draft
---

# Aegis Telegram — Substrate Command Surface (v0.10)

## Motivation

The Telegram frontend has stayed at its v0.2 vintage: agent
spawn/close, plain-text routing to an active session, status-line
edits during turns. Everything the substrate has shipped since —
queues, schedules, budgets, canvases, terminals, groups, the remote
plane, wire callbacks — has zero Telegram exposure. From the phone,
the user can talk to an agent and that's it. To check whether a
queue is backed up, whether a schedule is armed, whether a budget
tripped, they have to SSH to a serve and run the CLI.

This spec ships the **substrate command surface** for Telegram:
nine new chat commands that read (and where safe, fire) the
substrate resources that have grown around the bot. The renderer
overhaul, voice / file I/O, and substrate-level push notifications
are explicit non-goals for this round — they live in v0.11, v0.12,
v0.13 brainstorm rounds.

Concrete use cases:

- *"is impl backed up?"* → `/queue list`
- *"did the nightly build run?"* → `/schedule show nightly-build`
- *"did I trip a budget?"* → `/budget list`
- *"fire the morning briefing now"* → `/schedule run morning-briefing`
- *"is vps reachable?"* → `/peers`

## Non-goals (explicit)

- **No `/queue cancel <task_id>`.** `QueueManager.cancel(task_id)` is
  a substrate side-quest. Land it when the use case appears; not
  v0.10.
- **No `/schedule enable/disable`.** Both edit YAML on disk and
  deserve operator deliberation, not a mobile tap.
- **No canvas / terminal / workflow / group commands.** Those
  resources are sit-at-keyboard ops; mobile exposure is low-value.
  Add later if usage data argues for it.
- **No cross-host queue inspection.** There is no
  `GET /remote/v1/queue` HTTP endpoint. Adding one is a 30-min task
  but it widens the substrate surface — landing as a v0.10.x
  follow-up once this round ships.
- **No `/help` topic groupings beyond per-resource.** Flat list is
  fine for 14 commands.
- **No confirmation dialogs on mutations.** `/schedule run` fires
  immediately. Recovery: the next regular tick is unaffected by an
  errant manual fire.
- **No `format.py` overhaul.** MarkdownV2-escape-everything path
  stays only for the existing agent-reply chunker. New commands
  emit plain text. The renderer overhaul is bucket B from the v0.10
  brainstorm critique and gets its own round.
- **No voice / file I/O.** Bucket C from the critique; separate
  round. The renderer overhaul is bucket D from the critique;
  separate round.
- **No observer-list refactor.** Bucket D from the critique; the
  new commands don't observe sessions, so they sidestep the bug
  that bucket fixes.

## Architecture overview

One new module + a small refactor of one existing one. Zero
substrate-side changes.

```
   ┌───────────────────────────────────────────────────────────────┐
   │  aegis serve                                                   │
   │    cli.py constructs:                                          │
   │      bridge (_PlaneBridge: queue_manager, scheduler, ...)      │
   │      cfg    (AegisConfig: cfg.remotes → RemoteSpec map)        │
   │    Both are threaded into TelegramFrontend(...).               │
   │                                                                │
   │  src/aegis/telegram/                                           │
   │    bot.py            (unchanged)                               │
   │    format.py         (unchanged)                               │
   │    frontend.py       (refactored: _command → registry dispatch)│
   │    commands.py       (NEW: registry + handlers)                │
   └───────────────────────────────────────────────────────────────┘
```

- **`src/aegis/telegram/commands.py` (new).** A module-level dict
  `COMMANDS: dict[str, Command]` populated by `register(...)` calls
  at import time. Each `Command` carries `name` (verb +
  optional subcommand, e.g. `"queue list"`), `summary` for `/help`,
  `detail` for `/help <name>`, and `handler` (async, signature
  `(ctx: CmdContext, args: list[str]) -> None`).
- **`frontend.py::_command` refactor.** Collapses from a 30-line
  elif chain to a ~15-line dispatcher: split tokens, pull out
  `@peer`, look up longest-prefix match in `COMMANDS`, call its
  handler. The five existing verbs (`/new`, `/close`, `/interrupt`,
  `/agents`, `/sessions`) migrate into the registry alongside the
  new ones — uniform shape, single source of truth for `/help`.
- **`TelegramFrontend.__init__` grows two params: `bridge`, `cfg`.**
  The cli.py wire-up at `cli.py:284` adds them. If `remote_plane` is
  not configured (no plane bridge exists), construct a minimal
  bridge with just `queue_manager` + `scheduler`; handlers that
  need other fields degrade with a clear error.
- **No `format.py` changes.** Handlers emit plain text via
  `ctx.reply(text)`. Tabular data is wrapped in a fenced code block
  by the handler itself (` ```\n<table>\n``` `) so Telegram renders
  it monospace without invoking the MarkdownV2 escape path.

## Command registry

```python
# src/aegis/telegram/commands.py

@dataclass(frozen=True)
class CmdContext:
    bridge:  Any                           # _PlaneBridge
    cfg:     Any                           # AegisConfig
    manager: SessionManager
    target:  str | None                    # @peer name; None for local
    reply:   Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Command:
    name:    str       # full verb + subcommand: "queue list", "schedule run"
    summary: str       # one-line for /help
    detail:  str       # multi-line for /help <name>
    handler: Callable[[CmdContext, list[str]], Awaitable[None]]


COMMANDS: dict[str, Command] = {}


def register(cmd: Command) -> Command:
    if cmd.name in COMMANDS:
        raise ValueError(f"duplicate Telegram command {cmd.name!r}")
    COMMANDS[cmd.name] = cmd
    return cmd
```

### Dispatch path

In `frontend._command(text)`:

```python
head, _, rest = text.partition(" ")
verb = head.lstrip("/")
tokens = rest.split()

# Pull out @<peer>; everything else stays in args.
target: str | None = None
args: list[str] = []
for t in tokens:
    if t.startswith("@") and target is None:
        target = t[1:] or None      # empty "@" -> handler errors
    else:
        args.append(t)

# Longest-prefix match: try "<verb> <args[0]>" before "<verb>".
key2 = f"{verb} {args[0]}" if args else None
cmd = COMMANDS.get(key2) if key2 else None
if cmd is not None:
    args = args[1:]
else:
    cmd = COMMANDS.get(verb)

if cmd is None:
    # Fall back to legacy /<handle> alias-routing (existing behavior).
    return await self._legacy_handle_alias(head, rest)

ctx = CmdContext(bridge=self._bridge, cfg=self._cfg,
                 manager=self._m, target=target, reply=self._reply)
await cmd.handler(ctx, args)
```

### Cross-host (`@peer`) resolution helper

```python
def resolve_remote(ctx: CmdContext) -> tuple[str, "RemoteSpec"] | None:
    """If ctx.target is set, look it up in cfg.remotes and return
    (target_name, spec). Surface a `reply()` error if unknown.

    Returns None when ctx.target is None (i.e., local execution).
    """
    if ctx.target is None:
        return None
    remotes = getattr(ctx.cfg, "remotes", {}) or {}
    if ctx.target not in remotes:
        return None     # handler should reply error
    return ctx.target, remotes[ctx.target]
```

Each handler that supports cross-host calls `resolve_remote(ctx)` at
the top; if `ctx.target is not None and resolve_remote(...) is None`,
it replies with `unknown peer '<name>'; known: <list>`.

## Resource verbs

Nine new commands, plus the five existing verbs migrated into the
registry (`new`, `close`, `interrupt`, `agents`, `sessions`).

### `/queue list`

**Local only in v0.10** (no `GET /remote/v1/queue` exists yet).

Walks `bridge.queue_manager._queues`. For each, summarizes depth +
in-flight count + most recent completed task's status.

```
QUEUE       AGENT       DEPTH   IN-FLIGHT   LAST
impl        opus        0       0           ✓ 17:42  task#abc123
fast        haiku       2       1           ⏳ 18:01  task#def456
research    opus        0       0           — none
```

If `@peer` set: reply `▸ /queue list not yet supported cross-host
(local only). Drop @<peer>.`

### `/queue show <name>`

**Local only in v0.10.**

Reads `bridge.queue_manager._pending[name]` + `._inflight[name]` +
the last 10 records of the queue's JSONL. Format:

```
queue: impl  (agent: opus, max_parallel: 2)

IN-FLIGHT
  ⏳ task#abc123  worker:lucid-knuth  started 17:42  payload="implement…"

PENDING
  ○ task#def456  enqueued 17:45 by agent:caller  payload="fix…"

RECENT
  ✓ task#ghi789  completed 17:38  duration 4m12s
  ✗ task#jkl012  failed    17:30  duration 1m05s
```

### `/schedule list [@peer]`

Local: `bridge.scheduler.snapshot()`. Remote: `remote_schedule_list(spec)` (v0.8 client).

```
NAME              SOURCE   NEXT FIRE              ENABLED  FIRES
nightly-build     pushed   2026-05-27T02:00:00Z   ✓        47
weekly-report     inline   2026-05-31T08:00:00Z   ✓        12
ad-hoc-test       pushed   —                      ✗        0
```

### `/schedule show <name> [@peer]`

Local: `bridge.scheduler.get(name)`. Remote:
`remote_schedule_show(spec, name)`. Emits the full spec dict plus
runtime fields (next_fire, last_fire, fire_count, in_flight,
enabled, source, pushed_from, pushed_at).

### `/schedule run <name>`

**Local only.** Calls `bridge.scheduler.fire_now(name)`. Replies:

```
▸ fired schedule "nightly-build"
  next regular fire still at 2026-05-27T02:00:00Z
```

If `@peer` is set: `▸ schedule run not yet supported cross-host
(this serve only). Drop @<peer>.`

### `/budget list [@peer]`

Local: walks `bridge.queue_manager._queues`, runs `evaluate_budgets`
for each queue that has budgets. Remote: `remote_budget_list(spec)`
(v0.9 client, currently in-flight).

```
QUEUE       BUDGETS   STATUS                            UNBLOCKS
impl        4         ⛔  $1.23/$1.00 1h                18:42Z
review      2         ✓   $4.10/$10.00 24h              —
fast        0         —   no budget                     —
```

### `/budget show <queue> [@peer]`

Full `Decision`. Local: `evaluate_budgets` over the queue's JSONL
tail. Remote: `remote_budget_show(spec, queue)`. Emits one row per
`BudgetCheck`:

```
budget for queue 'impl'

CONSTRAINT     LIMIT      SPENT      WINDOW   HEADROOM   STATUS
usd            1.00       1.23       1h       -0.23      ⛔
usd            10.00      4.50       24h      5.50       ✓
output_tokens  500000     612340     1h       -112340    ⛔
usd            50.00      18.20      7d       31.80      ✓

blocked by 2 budget(s); unblocks at 19:10Z
```

### `/peers`

Iterates `cfg.remotes`. For each, shows name + URL + auth (token
yes/no) + reachable check. Reachability is a `GET <url>/remote/v1/`
with a 3s timeout — any HTTP response (even 404) means "reachable";
connection error means "unreachable".

```
NAME      URL                     AUTH    REACHABLE
vps       http://100.64.0.5:8556  token   ✓
desktop   http://100.64.0.3:8556  —       ✗ unreachable
```

No `@peer` argument (the command is about peers themselves).

### `/help [command]`

Registry-driven.

- `/help`: walks `COMMANDS`, groups by resource (the first whitespace
  -delimited token of `name`), prints `name + summary` per row.
- `/help queue`: prints all commands matching `name.startswith("queue ")`
  plus their `summary`. No detail body (the resource itself is the
  group label, not a command).
- `/help queue list`: prints the command's `detail` (multi-line).

### Existing verbs (migrated, not new)

`/new [slug]`, `/close`, `/interrupt`, `/agents`, `/sessions` move
into the registry. Behavior identical to v0.9. The migration is
mechanical: each becomes a `register(Command(name=..., handler=...))`
call; the handler body is lifted from the existing
`frontend._command` elif branch.

## Plumbing

### Bridge threading

`_PlaneBridge` (dataclass in `cli.py:138`) already carries
`queue_manager`, `scheduler`, `inbox_router`, `workflow_registry`,
`state_root`. The remote-plane wire-up at `cli.py:167` constructs it
when `remote_plane` is configured. For Telegram-only deployments
(no `remote_plane`), construct a minimal bridge with just
`queue_manager` + `scheduler` fields populated; everything else
stays at the dataclass default (`None`). Handlers that need
`state_root` (for `/queue show`'s JSONL tail read) check for `None`
and reply with `"queue inspection requires state_dir to be
configured on this serve"`.

### Config threading

`cfg` (the `AegisConfig` returned by `load_config`) is already in
scope at the Telegram wire-up point. Pass it to the frontend ctor.
Handlers read `cfg.remotes` directly.

### Constructor change

```python
# Before
TelegramFrontend(bot, mgr, chat_id=tg.chat_id,
                 auto_prompt=tg.auto_prompt)

# After
TelegramFrontend(bot, mgr, bridge, cfg,
                 chat_id=tg.chat_id, auto_prompt=tg.auto_prompt)
```

One callsite; one-line change.

## Output formatting

Three patterns:

- **Status / mutation confirmations** — short plain text, one or two
  lines. Examples: `▸ fired schedule "nightly-build"`,
  `▸ unknown peer 'vps'; known: [desktop]`. Sent via
  `ctx.reply(text)` with no `parse_mode`.
- **Tables** — wrapped in a fenced code block by the handler:
  `ctx.reply(f"```\n{table}\n```")`. Telegram renders monospace
  without requiring MarkdownV2 escapes (fenced code is a separate
  Markdown grammar that doesn't escape its body). If the table
  exceeds 4096 chars, the handler calls `chunk()` and emits each
  part wrapped separately. The chunker's existing escape path is
  not used — we don't pass these through `escape_md`.
- **Multi-line details** — `/schedule show`, `/budget show`,
  `/queue show` — same pattern as tables (fenced) with a
  human-readable header line above.

Mixing fenced + non-fenced lines in one message is fine; Telegram
parses them per-line.

## Error model

| Condition                                | Reply |
|------------------------------------------|-------|
| Unknown verb                             | Falls through to `/<handle>` alias; if that fails: `no session 'X' — /sessions`. Existing behavior. |
| Bad subcommand (`/queue ghost`)          | `unknown subcommand 'ghost' for /queue; /help queue` |
| Missing required arg (`/queue show`)     | `usage: /queue show <name>` |
| Unknown `@peer`                          | `unknown peer 'vps'; known: [desktop, …]` |
| `@peer` on local-only command            | `▸ <command> not yet supported cross-host (local only). Drop @<peer>.` |
| Substrate error from local call          | `▸ error: <exception message>` (one line; full traceback to log) |
| Remote 4xx/5xx                           | Surface the body's `error` field verbatim. Same shape v0.8 already uses. |
| Empty Telegram response                  | Logged at `bot.py` layer; user sees nothing — same as today. |

## Testing

Three layers, same shape as the v0.9 plan but smaller:

- **Unit per handler.** Each handler is independently callable. The
  test constructs a fake bridge (using existing `StubSessionManager`
  + an in-memory `QueueManager` with seeded queues + an in-memory
  `Scheduler` snapshot fixture), calls the handler with `args=[...]`,
  collects replies via a list-collecting `reply` callable, asserts
  the output's content and shape (fenced/plain).
- **Dispatcher tests.** `@peer` parsing — single, double (only first
  taken), empty (`@`); longest-prefix matching (`/queue list` resolves
  before `/queue` if both registered, the second wouldn't exist in
  v0.10); unknown verb falls through to legacy `/<handle>`
  alias-routing; verb migration sanity (every existing `/new`,
  `/close` etc. still routes after the registry refactor).
- **Smoke via `tests/test_telegram_frontend.py` extension.** Fire a
  fake update (`handle_update({...})`) for each new command; assert
  the right `_bot.send_message` call sequence.

No live tests this round (Telegram-API live tests are out of scope;
the existing `tests/test_telegram_bot.py` covers the BotClient
contract).

## Implementation sketch

### Touched files

- `src/aegis/telegram/commands.py` (new) — `Command`, `CmdContext`,
  `COMMANDS`, `register`, `resolve_remote`. Plus 14 handler
  functions registered at module import.
- `src/aegis/telegram/frontend.py` — `_command` collapses to
  registry dispatcher (~15 lines). `__init__` grows `bridge` + `cfg`
  params. The five existing verbs' bodies move to `commands.py` as
  handlers. The `_legacy_handle_alias` method splits out the
  `/<handle>` alias-routing branch (the only thing that stays
  outside the registry).
- `src/aegis/cli.py` (one call-site) — `TelegramFrontend(bot, mgr,
  bridge_or_minimal, cfg, chat_id=..., auto_prompt=...)`. Plus a
  small helper to construct the minimal bridge when `remote_plane`
  is not configured.
- `tests/test_telegram_frontend.py` — extend with the migrated
  verbs' tests (existing) + dispatcher edge cases.
- `tests/test_telegram_commands.py` (new) — one test class per
  resource (queue / schedule / budget / peers / help), each
  covering local + remote (where applicable) + error paths.

### State changes

None. The frontend stays in-process; no new disk state. The bot's
update-offset persistence (bucket D from the critique) is deferred
to the v0.11 correctness round.

## Future extensions (noted, not built)

- `aegis_handoff(target_handle=…, target="<peer>")` — cross-host
  handoff to a live remote handle. (v0.8 spec already flagged this.)
- `GET /remote/v1/queue` HTTP endpoint → unlocks cross-host
  `/queue list @vps`, `/queue show <n> @vps`. Small follow-up
  (v0.10.x) once this round ships.
- `/queue cancel <task_id>` — needs `QueueManager.cancel(task_id)`
  substrate-side. Side-quest.
- `/schedule enable/disable`, `/budget`-mutations,
  `/canvas read/write`, `/term tail`, `/group status`, `/workflow
  status/cancel` — bigger surface; add when use cases appear.
- Telegram-side substrate-event push (bucket E from the critique)
  — `notify()` API on the substrate that fires Telegram pings on
  schedule fires, budget trips, etc. Separate brainstorm.
- The renderer overhaul (bucket B), voice/file I/O (bucket C), and
  correctness fixes (bucket D) are their own brainstorm rounds.

## Open questions

1. **Reachability check for `/peers`.** A `GET <url>/remote/v1/`
   probe with 3s timeout — simple and good enough. Alternative is a
   dedicated `/remote/v1/health` endpoint that responds with the
   peer's `peer_name`. The latter is cleaner but expands the
   substrate API surface for one v0.10 command. Lean: keep it as a
   3s ping in v0.10; add a real `/health` endpoint later if peers
   accumulate.

2. **Migrating `/new`, `/close`, `/interrupt` into the registry.**
   These verbs currently mutate `self._active` (the active-session
   pointer). The registry handlers don't have direct access to
   `self`; they'd mutate via `ctx.manager` (no `_active` field)
   plus a small bag of frontend state passed in `ctx`. Option:
   extend `CmdContext` with a `frontend_state: Any` field that's
   the frontend itself, and handlers do `ctx.frontend_state._active
   = handle`. Slightly ugly but localised — the alternative is
   keeping these five verbs out of the registry, which defeats
   "single source of truth for /help." Lean: pass the frontend
   (typed loosely) into the context so migration is clean.

3. **Should the migrated `/<handle>` alias-routing live in the
   registry?** It's not a fixed verb — it's a pattern (`/<any-handle>
   [text]`). If we put it in the registry it'd need a wildcard
   match. Keeping it out (as a fallback after registry lookup) is
   cleaner. Decided: stays out.
