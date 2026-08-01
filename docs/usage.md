# Usage

`aegis` opens a full-screen TUI. Type in the input box and press
`Enter` to send. Each tab is an independent agent session.

## Keys

| Key | Action |
|---|---|
| `Enter` | Send the input |
| `Ctrl+T` | New tab with the default agent profile |
| `Ctrl+N` | New tab — pick an agent profile from a modal |
| `Ctrl+W` | Close the active tab (closing the last quits) |
| `Ctrl+1`..`Ctrl+9` | Jump directly to tab N |
| `Ctrl+Tab` / `Ctrl+→` / `Ctrl+←` | Next / previous tab |
| `Ctrl+Shift+→` / `Ctrl+Shift+←` | Move the active tab one slot along the bar (`Alt+Shift+←→` alias) |
| `Drag a tab` | Reorder with the mouse — the tab follows the pointer across its neighbours |
| `Ctrl+D` | Open / close the queue dashboard |
| `Ctrl+R` | Session history — reopen a prior session (jump / resume / fresh) |
| `Ctrl+O` | Fuzzy file picker |
| `Escape` | Interrupt the active turn (or dismiss the dashboard / agent picker) |
| `Click on a block` | Copy that message / tool result to clipboard |
| `Ctrl+Q` | Quit |

## Input prefixes: `!` shell and `/` commands

Two leading characters route the input line to **aegis itself** instead of
the agent. The input outline **and text** change colour so you can tell at a
glance what a line will do: green message · magenta shell · blue command.

### `!` — shell escape

`!<command>` runs `<command>` in a local shell (in the session's project
root) and injects `$ <command>` plus its combined stdout/stderr into the
conversation as your next message — so the agent sees the result. The input
turns **magenta** while the line starts with `!`.

```
!git status --short
!ls -la src/
```

Output is capped, merges stderr, notes a non-zero exit, and times out after
60s. A bare `!` is a no-op.

### `/` — slash commands

`/<command>` is a **control command aegis executes directly** — it never
reaches the agent. The result renders as a `/`-glyph block in the transcript
(red when it fails). The input turns **bright blue** while the line starts
with `/`.

| Command | What it does |
|---|---|
| `/help` | List the available slash commands |
| `/sessions` | List live agent tabs (`handle · agent · state`) |
| `/agents` | List configured agent profiles (`name · harness · model · permission`) |
| `/spawn <agent> [prompt]` | Start a new top-level agent, optionally with an opening prompt |
| `/queue new <name> [agent]` | Create a queue |
| `/enqueue <queue> <payload>` | Drop a task on a queue |
| `/fork [prompt]` | Branch this conversation into a new tab |
| `/btw <question>` | Answer a side question from the recent window |
| `/peer <handle> [--cc] <question>` | Ask an idle peer (`@handle …` is sugar) |

Precedence: `!` shell escape > `/` slash command > a plain message to the
agent. An unknown command shows an error block pointing at `/help`.

### Asking without spending the conversation

Three commands sit on the same idea — a question you want answered *near*
this conversation without paying for it in context, money, or the turn you
are in the middle of.

- **`/fork [prompt]`** branches this pane into a new tab: a worker that
  already knows everything you have said. Refused mid-turn (a live turn's
  tail is a `tool_use` with no matching result, and a fork would inherit the
  dangling call), and the parent is left byte-identical. It carries the whole
  conversation, and costs about a dollar.
- **`/btw <question>`** answers from this pane's recent window and
  disappears. It never touches the harness session — it reads aegis's own log
  and makes one independent call — which is why it is legal mid-turn. The
  answer goes into the pane's scrollback and is *never* written to the
  session log, so side notes do not compound: the fifth `/btw` still sees
  real conversation rather than four of its own prior musings.
- **`@handle <question>`** asks an **idle** peer, sending it a cheap slice of
  where you are standing. The answer lands as a real turn in *their*
  transcript and a transient block in yours; your own agent neither sees it
  nor pays for it. Idle-only is the domain, not a limitation — the point is
  extracting value from a warm context that is currently producing nothing,
  and it is legal while your *own* tab is mid-turn, which is exactly when you
  have dead time to spend. Add `--cc` (at send time — the peer never decides
  this) to also land the answer in your own conversation.

`/btw` and `/peer` are **deferred**: they mount a placeholder that echoes
your question and answer in place, so nothing freezes while they run, and
`Esc` cancels the note before it clears a half-typed line or interrupts the
turn.

## Tabs

Each tab is an independent agent session with:

- A **generated alliterating handle** (`adjective-laureate` —
  `lucid-knuth`, `wry-hopper`, `brisk-blum`). Handles maximize variety
  within a session: no laureate is reused until the pool is exhausted,
  no adjective is reused until its pool is exhausted, and initial
  letters cycle so one letter never dominates.
- A **state dot**: green idle, amber working, red error.
- A **sticky `*`** when a backgrounded tab finishes — plus a terminal
  bell — so you notice background work completing.
- A **scrolling tab bar** that keeps the active tab in view, and that you
  can **reorder** — `Ctrl+Shift+←/→` moves the active tab one slot, or drag
  a tab with the mouse. The order is saved with the roster, so it survives a
  restart.

## The transcript

Each agent message, tool call, and tool result is a separate
**block**. Hover any block to see a tooltip; click to copy that block
verbatim to your clipboard — useful for grabbing tool outputs, error
messages, or generated code snippets.

While an agent is working, an inline **spinner + rotating verb + elapsed
timer** appears at the bottom of the transcript:

```
⠹ Crystallizing… (4.7s)
```

The verb rotates every few seconds (Thinking → Pondering →
Crystallizing → Synthesizing → …) so you can see the agent is still
alive even when it's silent.

## Status line & metrics

```
handle ·profile· model · permission   state   ↑<input> (<n>% cached) ↓<output> · ⚒ <tools> · <turn> / <session>
```

- `↑` is the **true** input the model ingests — uncached input **plus**
  cache creation **plus** cache read. On a typical Claude session this
  is often >90% cached.
- `<n>% cached` is the fraction of `↑` that came from cache (not
  re-billed at full rate).
- `↓` is total output tokens this session.
- `⚒` is the count of tool calls this session.
- `<turn>` is the wall-clock time of the most recent turn; `<session>`
  is the total wall-clock since this tab opened.

Numbers are **provisional** (`~` prefix) while a turn is streaming and
**exact** at turn end.

## Usage analytics (`aegis usage`)

Where the status line reports *this* session, `aegis usage` aggregates
**every** session log under `.aegis/state/sessions/` into a cost and
usage dashboard. It is read-only and recomputes on each run. The same
dashboard is available inside a running session as the **`/usage`** slash
command (`/usage`, `/usage tools`, `/usage sessions`, `/usage month|dow|hour`),
rendered as a transcript block — identical data in the TUI and web client.

```
aegis usage                     # dashboard: cost, averages, models, tools, top sessions
aegis usage --by month|dow|hour # turns bucketed over time (local timezone)
aegis usage --sessions          # cost-per-session distribution + top 15
aegis usage --tools             # per-tool average turn cost vs. baseline
aegis usage --since 2026-07-01  # only sessions active on/after a date
aegis usage --session <handle>  # a single session
aegis usage --model <id>        # filter to one model
aegis usage --tz <IANA>         # override the local timezone for bucketing
```

**Two-layer cost model.** The headline **billed** figure is authoritative
— the segment-aware sum of each turn's `cost_usd` (claude-code's own
`total_cost_usd`, which is cumulative-with-resets across resumes). Beneath
it, an **analytical split** prices the token counts against the model
registry to separate *generation* (input + cache-write + output) from
*context replay* (cache reads). Replay is a genuine billed cost but is
context re-read, not new work — so it is shown apart from generation
rather than folded into the headline. On long Claude sessions replay is
often the majority of the token-priced total.

Sessions with no recorded `cost_usd` (older logs) fall back to a
token-priced estimate, flagged `~est`. The model per session comes from
each log's `SystemInit.model`; sessions predating that field are attributed
to the `.aegis.yaml` `default_agent`'s model.

## Themes

The default **Ink** theme is calm near-black with one amber accent.
Themes are a Textual-native registry; more are drop-in additions.

## Interrupting

Press `Escape` to interrupt the active turn. The harness is notified;
the agent stops at the next safe point (after the in-flight tool call,
typically within a second). The TUI returns to idle and you can send
again.

If a modal screen is on top (the queue dashboard, the agent picker),
`Escape` dismisses the modal instead — the interrupt path only fires
on the default screen.

## Queue dashboard

When queues are configured in `.aegis.yaml`, a one-line **strip** sits
just above the status bar in every conversation showing live per-queue
depth and the most recent in-flight worker. Press `Ctrl+D` to expand
into a full-screen modal with `QUEUES / IN-FLIGHT / QUEUED / RECENT`
bands and a detail panel that tails the selected worker's assistant
text. See [Queues → Dashboard](queues.md#dashboard-ctrld) for the full
key map.

When a handoff or queue callback lands on the active agent, a
distinct `✉` block appears in the transcript with sender, status,
timestamp, and a body preview — before the agent reacts.

## Persistence

`aegis` reopens the **last workspace** by default — same tabs, same
profiles, same order, with each underlying model session genuinely
**resumed** (the agent's memory is intact, not a transcript replay).
Pass `--clean` to start fresh and overwrite the persisted workspace.

```bash
aegis           # resume the last workspace
aegis --clean   # start fresh (no resume)
```

What's persisted, under `.aegis/state/` next to your `.aegis.yaml`:

- **`workspace.json`** — the tabs that were open: handle, profile,
  display order, which tab was active.
- **`sessions/<handle>.jsonl`** — per-tab append-only event log used by
  the TUI to rebuild each pane's transcript.

Limitations:

- **Driver support.** Claude Code resumes via `claude --resume`; Gemini
  and OpenCode resume via ACP `loadSession` (the spawned agent must
  implement it — if it doesn't, the tab opens with a failure banner).
- **Cwd-bound.** Claude resume is tied to the working directory of the
  original session. Moving or renaming the project breaks resume for
  that workspace; use `--clean` to recover.
- **Terminals + file tabs.** Terminals re-spawn as fresh shells over
  their existing ledger; file tabs re-open the file at the saved path
  (dirty buffers and cursor position are NOT preserved). Both restore
  whether or not any agent tabs resumed.
- **Workers not resumed.** Queue workers and workflow runs are not part
  of the workspace snapshot; only interactive tabs are restored.

## Headless mode

If you want the routing plane (sessions, queues, MCP) without the TUI,
run `aegis serve`. See [Configuration](configuration.md#headless-telegram)
for the Telegram bridge.
