# Telegram

`aegis serve` includes a built-in Telegram bot that lets you drive the
full substrate from your phone. v0.10 extends the original session-spawn
verbs with a **substrate command surface**: nine new commands that expose
queues, schedules, budgets, peers, and help — every resource the
substrate has grown since v0.2 is now reachable from the chat.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the
   token.
2. Find your personal chat ID (send any message to the bot and call
   `getUpdates`, or use the `@userinfobot` helper).
3. Add to `.aegis.py`:

```python
# .aegis.py
telegram_token   = "..."        # or set AEGIS_TELEGRAM_TOKEN
telegram_chat_id = 123456789    # the single allowed chat

# Optional: append a brevity hint to every bare-text message.
# Set to "" to disable.
# auto_add_to_telegram_prompt = "Be concise. Reply in one paragraph."
```

4. Start the serve:

```bash
aegis serve
```

A systemd unit template lives at `scripts/aegis-serve.service`.

### Bot account requirements

- A **private bot** — only the single `telegram_chat_id` is accepted.
  Any message from a different chat ID is silently dropped.
- The bot needs no special permissions beyond `sendMessage` /
  `getUpdates`. No inline mode, no group membership needed.
- Webhook mode is not supported; the bot uses long-polling internally.

---

## Session verbs (v0.2+, registry-wired in v0.10)

These five commands have been available since v0.2. In v0.10 they were
migrated into the command registry alongside the new substrate verbs —
behavior is unchanged.

| Command | Action |
|---|---|
| `/new [agent]` | Spawn a new session (defaults to `default_agent`) |
| `/close [handle]` | Close a session (default: the active one) |
| `/interrupt` | Interrupt the active turn |
| `/agents` | List configured agent profiles |
| `/sessions` | List open sessions with their state |
| `/<handle> text…` | One-shot to a specific session — doesn't move the active pointer |
| bare text | Sent to the active session, with `auto_add_to_telegram_prompt` appended |

---

## Substrate command surface (v0.10)

Nine new commands reach the substrate resources directly. They follow
a consistent two-part shape: `/resource subcommand [args] [@peer]`.

### Output style

- **Status and mutation replies** — plain text, one or two lines.
- **Tables and multi-line details** — wrapped in a fenced code block so
  Telegram renders monospace without any MarkdownV2-escape gymnastics.
- **Errors** — short plain text explaining the problem and, where
  relevant, what to try instead.

---

### Queue commands

#### `/queue list`

Shows all configured queues with depth, in-flight count, and the most
recent task's status.

```
QUEUE       AGENT       DEPTH   IN-FLIGHT   LAST
impl        opus        0       0           ✓ 17:42  task#abc123
fast        haiku       2       1           ⏳ 18:01  task#def456
research    opus        0       0           — none
```

**v0.10 limitation:** local only. The substrate does not yet expose a
`GET /remote/v1/queue` endpoint. If you add `@peer`, the bot replies:

```
▸ /queue list not yet supported cross-host (local only). Drop @<peer>.
```

#### `/queue show <name>`

Full detail on one queue: in-flight tasks, pending tasks, and the last
10 records from the queue's JSONL audit log.

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

**Local only** in v0.10 for the same reason as `/queue list`.

---

### Schedule commands

#### `/schedule list [@peer]`

Lists all schedules with next fire time, enabled state, and fire count.

```
NAME              SOURCE   NEXT FIRE              ENABLED  FIRES
nightly-build     pushed   2026-05-27T02:00:00Z   ✓        47
weekly-report     inline   2026-05-31T08:00:00Z   ✓        12
ad-hoc-test       pushed   —                      ✗        0
```

Add `@<peer>` to inspect a remote serve's schedule table:

```
/schedule list @vps
```

#### `/schedule show <name> [@peer]`

Full detail on one schedule: the full spec plus runtime fields (next
fire, last fire, fire count, in-flight flag, enabled state, source
classification, push provenance).

```
/schedule show nightly-build
/schedule show nightly-build @vps
```

#### `/schedule run <name>`

Force-fire a schedule immediately. Does not affect its next regular
fire. **Local only** in v0.10 — cross-host fire is not yet supported.

```
▸ fired schedule "nightly-build"
  next regular fire still at 2026-05-27T02:00:00Z
```

If you add `@peer`:

```
▸ schedule run not yet supported cross-host (this serve only). Drop @<peer>.
```

---

### Budget commands

#### `/budget list [@peer]`

Summary of budget state for every queue that has budgets configured.

```
QUEUE       BUDGETS   STATUS                            UNBLOCKS
impl        4         ⛔  $1.23/$1.00 1h                18:42Z
review      2         ✓   $4.10/$10.00 24h              —
fast        0         —   no budget                     —
```

Add `@<peer>` to inspect a remote serve's budget state:

```
/budget list @vps
```

#### `/budget show <queue> [@peer]`

Full decision for one queue: every configured budget entry with limit,
amount spent in the window, headroom, and pass/block status.

```
budget for queue 'impl'

CONSTRAINT     LIMIT      SPENT      WINDOW   HEADROOM   STATUS
usd            1.00       1.23       1h       -0.23      ⛔
usd            10.00      4.50       24h      5.50       ✓
output_tokens  500000     612340     1h       -112340    ⛔
usd            50.00      18.20      7d       31.80      ✓

blocked by 2 budget(s); unblocks at 19:10Z
```

---

### Peers

#### `/peers`

Lists every configured remote with URL, auth status, and a live
reachability probe (3s timeout; any HTTP response = reachable).

```
NAME      URL                     AUTH    REACHABLE
vps       http://100.64.0.5:8556  token   ✓
desktop   http://100.64.0.3:8556  —       ✗ unreachable
```

No `@peer` argument — the command is about peers themselves.

---

### Help

#### `/help`

Lists every registered command grouped by resource, with a one-line
summary per command.

#### `/help <resource>`

Lists all commands for a resource (e.g. `/help queue`, `/help schedule`).

#### `/help <command>`

Full detail for a specific command (e.g. `/help queue show`,
`/help schedule run`).

---

## `@<peer>` cross-host syntax

Commands that support cross-host inspection accept an `@<peer>` token
anywhere in the argument list:

```
/schedule list @vps
/budget show impl @vps
```

The peer name must match a key in the `remotes:` section of your
`.aegis.yaml`. If the name is unknown, the bot replies:

```
unknown peer 'vps'; known: [desktop, builder]
```

Commands that are **local-only in v0.10** (`/queue list`, `/queue show`,
`/schedule run`) return a clear message instead of silently ignoring the
`@peer` token.

---

## Error reference

| Situation | Reply |
|---|---|
| Unknown verb | Falls through to `/<handle>` alias routing; if no session matches: `no session 'X' — /sessions` |
| Bad subcommand (`/queue ghost`) | `unknown subcommand 'ghost' for /queue; /help queue` |
| Missing required arg (`/queue show`) | `usage: /queue show <name>` |
| Unknown `@peer` | `unknown peer 'vps'; known: [desktop, …]` |
| `@peer` on local-only command | `▸ <command> not yet supported cross-host (local only). Drop @<peer>.` |
| Substrate error | `▸ error: <message>` (one line; full traceback goes to the serve log) |
| Remote HTTP error | The remote's `error` field, verbatim |

---

## FAQ

**What's not supported in v0.10?**

- `/queue list @<peer>` and `/queue show @<peer>` — the substrate has no
  `GET /remote/v1/queue` endpoint yet. Planned for v0.10.x.
- `/schedule run @<peer>` — remote fire-now is not yet wired. Land when
  the use case arrives.
- `/schedule enable` / `/schedule disable` — these edit YAML on disk and
  deserve deliberate operator action, not a mobile tap.
- `/queue cancel <task_id>` — needs `QueueManager.cancel(task_id)` on the
  substrate side. Side-quest for a later round.
- Canvas, terminal, workflow, group commands — sit-at-keyboard ops. Telegram
  exposure is low-value for v0.10.
- Substrate push notifications (budget trips, schedule fires, etc.) —
  a dedicated v0.11/v0.12 round with a `notify()` API on the substrate.
- Voice note / file input/output — a separate brainstorm round.

**Can I use `aegis serve` without Telegram?**

Yes. Telegram is entirely optional. Omit `telegram_token` and
`telegram_chat_id` from `.aegis.py` and the serve runs as a pure
headless substrate (MCP plane + HTTP remote plane).

**Is there a webhook mode?**

No. The bot uses long-polling. Webhook support can be added later if
you're running the serve behind a public-IP reverse proxy.

**The bot ignores my messages.**

Check that `telegram_chat_id` matches your personal chat ID, not the
bot's own ID. Any message from an unrecognized chat is dropped without
a reply.

**Can multiple users share one bot?**

No. `telegram_chat_id` is a single integer. If you need multi-user
access, run separate `aegis serve` instances with separate bots.
