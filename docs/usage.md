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
| `F3` | Open / close the dashboard sidebar — every tab at once, since it's a reading mode, not a per-tab widget (`/tasks` does the same) |
| `Ctrl+R` | Session history — reopen a prior session (jump / resume / fresh) |
| `Ctrl+O` | New file browser tab — files newest-first, filter, `F3` tree sidebar; pick one and the tab becomes the editor (`b` / `Escape` go back) |
| `Escape` | Interrupt the active turn (or dismiss the dashboard / agent picker) |
| `Alt+↑` / `Alt+↓` | Scroll the transcript one line |
| `Ctrl+↑` / `Ctrl+↓` | Jump to the start of the previous / next message — one press from the tail parks the last agent message on the first row |
| `Alt+End` | Back to the live tail (and follow the running turn again) |
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
| `/spawn <agent> [prompt]` | Start a new top-level agent, from where you're standing |
| `/queue new <name> [agent]` | Create a queue |
| `/enqueue <queue> <payload>` | Drop a task on a queue |
| `/fork [prompt]` | Branch this conversation into a new tab |
| `/btw <question>` | Answer a side question from the recent window |
| `/peer <handle> [--cc] <question>` | Ask an idle peer (`@handle …` is sugar) |

Precedence: `!` shell escape > `/` slash command > a plain message to the
agent. An unknown command shows an error block pointing at `/help`.

### Asking without spending the conversation

Four commands sit on the same idea — work you want done *near* this
conversation without paying for it in context, money, or the turn you are
in the middle of. What separates them is how much of where you are
standing travels with the question, and what it costs to send it.

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

- **`/spawn <agent> <prompt>`** is the fourth, and the only one that starts
  a *cold* agent — no inherited conversation, no inherited bill. With a
  prompt it now carries the same cheap slice `@peer` sends: where it was
  spawned from, a bounded tail of this pane, and `aegis_read_peer` to pull
  the rest. That is what makes `/spawn opus please verify this test` a
  sentence — the new agent can see which test, and go read the conversation
  when the tail is not enough. Unlike `@peer`, it is told to go *do* the
  work and hand the result back, which is what you were paying a new agent
  for. With no prompt, or from a pane with nothing in it yet, nothing is
  attached.

`/btw` and `/peer` are **deferred**: they mount a placeholder that echoes
your question and answer in place, so nothing freezes while they run, and
`Esc` cancels the note before it clears a half-typed line or interrupts the
turn.

## Recaps and the loop judge

Two surfaces read what a turn actually **did** — commits, files written,
plan movement — rather than what it said about itself.

- **A one-line recap** lands after any turn that moved the substrate.
  `/recap` asks for the bigger version on demand: a building / done /
  remaining block about the whole session.
- **The loop judge** decides whether an armed `/loop` continues, returning
  `continue`, `done` or `stuck`. `aegis_loop_stop` is the *agent's* claim
  that it is finished, and the judge is free to reject it; your own
  `/loop stop` stays authoritative.

Both are off the same facts, and both matter for the same reason: an agent
grading its own homework from inside the tunnel it has been in for N turns
is how a loop gets reaped with the user-visible half unbuilt.

The automatic recap gates on **substrate movement, not turn count** — turn
count is what makes recaps pile up identically in a conversation of
questions and reads. It is also detached and cancellable: a measured ~7s
one-shot cannot be allowed to stall every turn boundary, and a new turn
drops an in-flight recap rather than rendering it late against a transcript
that has moved on. Like a `/btw` side note it lands in the pane's scrollback
and is never appended to the session log, so recaps never compound into
summaries of their own summaries.

Both are one-shot generation calls, so set
[`text_generation:`](configuration.md#text_generation-optional) to something
cheap. Turn either off with
[`recap:` / `loop_judge:`](configuration.md#recap-and-loop_judge-optional);
`recap: false` silences the automatic line but leaves `/recap` available.

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

## Session titles

A handle tells you *which* session; a title tells you what it is **about**.
Ten tabs reading `lucid-knuth`, `deep-dijkstra`, `wry-hopper` are fast to
switch between and impossible to choose between, so a session also carries
a short title — beside the handle, never instead of it.

That distinction is the whole design. The handle is **identity**: it is
`from_handle` on every MCP call, the inbox routing key, and half the
immutable log id. The title is only a label, so setting one moves nothing
and breaks no routing.

You get one three ways:

- **Automatically.** When a session's first turn finishes, aegis makes one
  cheap call to summarize what you asked for, and titles the session with
  it. Once per session, not per turn — a label that churns is worse than
  one that doesn't. Point `text_generation:` at a small profile in
  `.aegis.yaml` to keep this off your main model's bill:

    ```yaml
    text_generation: haiku
    ```

- **By hand.** `/title fix the eviction race`. Bare `/title` regenerates
  from where the conversation is *now* — worth doing when a thread has
  wandered eight turns from what it opened with. `/title --clear` drops it.
- **By the agent.** An agent can name itself with `aegis_title`, or set a
  name and a title together with `aegis_rename(new_handle, title=…)`.

Writes are ordered **`human > agent > auto`**. What you typed wins; an
agent cannot overwrite it, and the refusal says so rather than failing
quietly. That ordering is also the entire concurrency story — a title that
loses simply never lands, so a slow auto-generation arriving after you
typed one is discarded on arrival.

Titles are stored in the transcript, so they survive a restart, and
**`Ctrl+R`** shows and filters on them — which is where they earn the most,
since a history row has a whole line to spend. The active session's title
also sits in the status line.

Generation is best-effort by contract: if the model is having a bad day the
session simply stays untitled. It can never disturb a turn.

## The transcript

Each agent message, tool call, and tool result is a separate
**block**. Hover any block to see a tooltip; click to copy that block
verbatim to your clipboard — useful for grabbing tool outputs, error
messages, or generated code snippets.

A tool-call block clicks to expand its full arguments instead. On a
`Read`, `Write`, or `Edit` block, **`Ctrl+click` opens the file that
call touched** in a `FileTab` — a `Read` with an offset and an `Edit`
both land on their line (the edit's line is found by looking for the
text it replaced; if the file has moved on too far, the file still
opens, at the top). `Ctrl+click` on a backtick-wrapped filename in
prose does the same thing for whatever the agent named.

While an agent is working, an inline **spinner + rotating verb + elapsed
timer** appears at the bottom of the transcript:

```
⠹ Crystallizing… (4.7s)
```

The verb rotates every few seconds (Thinking → Pondering →
Crystallizing → Synthesizing → …) so you can see the agent is still
alive even when it's silent.

## The aegis layer in the transcript (`aegis comms`)

When an agent calls into the [MCP plane](mcp.md), the call reads as what it
is — an act aimed at a counterpart, in the layer's own colour:

```
⇄ weary-turing · "the render is yours"
⇉ general · task#01K2CA0F
⊙ exclusive · src/aegis/mcp/ · 3 paths
∘ claims
```

Nineteen glyphs name **semantic acts, not tools**, so `spawn`, `fork` and
`group_spawn` share one mark: what matters when you scan a transcript is
that a new agent appeared, not which verb produced it. Everything that
merely reads the room — `list_sessions`, `claims`, every `config_*` — takes
one pale `∘`, because polling is the single largest slice of aegis traffic
and giving it the weight of a conversation was the thing worth fixing.

Every one of those calls also leaves an **envelope** on disk: a daily JSONL
under `.aegis/state/comms/`, one record per call, carrying who called, the
typed counterpart, the family, the verb, a thread id, the outcome and the
duration. Read it back with:

```bash
aegis comms list                          # every call, oldest first
aegis comms list --handle weary-turing     # either end of the call
aegis comms list --thread 01K2CA0F         # one conversation across agents
aegis comms list --family conversation     # conversation | coordination
                                           # | introspection | admin
aegis comms list --since 2026-08-11T09:00Z
```

```
2026-08-11T15:22:04.881Z aegis-release-cut  handoff   agent:weary-turing  01K2C9T1
2026-08-11T15:22:41.019Z aegis-release-cut  enqueue   queue:general       01K2CA0F
2026-08-11T15:31:12.402Z (unattributed)     claims    -                   01K2CB33
```

`thread` adopts the substrate's own ids (`task_id`, `monitor_id`,
`claim_id`, `workflow_run_id`, …), so an `enqueue` and the callback that
lands in another agent's inbox twenty minutes later share one — which is
how you follow a delegation across two agents that never mention each
other.

**`from` is best effort, and the ledger says so.** The MCP server is
co-resident and shared, so there is no transport identity: a handle is a
parameter the agent passes by convention, and the tools that take none
(`aegis_list_sessions`, `aegis_claims`, `aegis_meta`, every `config_*`)
cannot be attributed at all. Those rows print `(unattributed)` rather than
a guess, because a fabricated attribution in an audit record is worse than
an honest gap.

## Status line & metrics

```
handle ·profile· model · permission   state   ctx <n> (<p>%) · ✂<n> · ↑<input> (<n>% cached) ↓<output> · ⚒ <tools> · <turn> / <session>
```

- `↑` is the **true** input the model ingests — uncached input **plus**
  cache creation **plus** cache read. On a typical Claude session this
  is often >90% cached.
- `<n>% cached` is the fraction of `↑` that came from cache (not
  re-billed at full rate).
- `↓` is total output tokens this session.
- `⚒` is the count of tool calls this session.
- `ctx` is how full the model's context window is — the **largest single
  sub-turn** of the most recent turn against the window for that model.
  It turns yellow past 50% and red past 75%. Note what it is not: the
  session's accumulated input. An agentic turn is many round trips, and
  charging the gauge for all of them made a 30-sub-turn turn read
  *1052%*.
- `✂` counts **compactions** — how many times the harness has dropped
  older context to keep going. It appears only once one has happened,
  yellow at one and red at two or more, and each one re-baselines `ctx`
  to the post-compaction size. Claude Code reports these exactly (a
  `compact_boundary` event); ACP harnesses have no equivalent signal, so
  there the counter simply stays absent rather than guessing.
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

## The task list

When an agent is working through a plan, a one-line **strip** above the
status bar shows a circle per task, how many are done, and what it is on
right now with a running clock:

```
tasks: ● ● ◐ ○ ○  2/5 · Fixing the task panel layout 1:36
```

The clock is **working time, not wall clock** — it accrues only while the
session is mid-turn, so a task left in progress overnight reports the
minute of work it actually got rather than nine hours of idling. The
in-progress circle spins on exactly that condition, so the rotation is a
literal rendering of the clock running.

Press **`F3`** (or type `/tasks`) for the **dock** beside the transcript:
one row per task with its working time, and any subagent's plan nested
underneath. It is a mode, and the mode is app-wide — every tab opens it
together, and a tab you open later comes up already in it — which is what makes a fan-out legible, since it shows which
of several parallel agents is still grinding. A task that never started
reads `—`, not `0:00`; the two mean different things.

The plan survives a restart: a resumed session replays its own transcript
and comes back with the tasks *and* their banked time intact.

Above it sits **REPOS** — which git repos the live agents are actually
writing to:

```
REPOS                              2
● aegis        main ~6 ↑6  calm-hopper
● Workspace    main ~2
```

`●` marks a repo this tab's agent is in, `·` one only its peers are in,
and the count turns amber when more than one live agent is writing to the
same tree — the collision you would otherwise find at `git diff`, hours
later. `~n` is uncommitted files, `↑n` unpushed commits (the "a job clones
`origin` and silently gets the old tree" failure, made visible), and a
detached `HEAD` or a rebase in flight replaces the branch in the error
colour.

Membership is learned from **writes only** — `Write` / `Edit` /
`NotebookEdit`, or their ACP equivalents by tool *kind*. Reads do not put
a repo on the board, or every repo an agent merely grepped would show up
and the section would stop meaning *work is happening here*. `Bash` is
deliberately missed: guessing write targets out of a shell command is a
heuristic, and one wrong row would make the whole section untrusted. A
repo on a remote [execution host](hosts.md) is listed but never probed —
the same path names a different tree there, so a local `git status` would
answer confidently and wrongly.

The panel's foot is a **SYSTEM** block: the CPU/RAM/disk meters, then the
date, time and locale, the directory this aegis is rooted at, and the build
it is actually running (`aegis 0.32.0+b78cb3d`). The last two are the pair
you go looking for rather than notice — under an editable checkout that
keeps moving, "which version am I running" and "which version is on disk"
diverge the moment a commit lands beneath a live TUI.

Other agents can see it too. The tab bar carries a compact `3/8` for any
tab with a plan, `aegis_list_sessions` rolls it up, and
[`aegis_peer_plan`](mcp.md) drills into a peer's full list — so an agent
deciding who to hand work to learns not just that a peer is busy but how
far along it is.

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
- **`sessions/<log id>.jsonl`** — per-tab append-only event log used by
  the TUI to rebuild each pane's transcript. The id is minted once at
  spawn and never changes, so renaming a session moves nothing.
- **`aegis.log`** — the process log. A different artifact from the
  transcripts: see below.

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

## The aegis log (`aegis logs`)

The session transcripts record what each **agent** said. `aegis.log`
records what **aegis itself** did — and, above all, the crashes it caught
on the way down.

That last part is the reason it exists. When the TUI dies, Textual prints
its traceback as it restores the terminal: on the alternate screen, after
the app has already stopped. It scrolls away, and a crash that leaves no
artifact can only be re-witnessed, never debugged.

```bash
aegis logs              # the last 200 lines
aegis logs --crashes    # only crash banners and their tracebacks
aegis logs -f           # follow
aegis logs --path       # print the file's path and exit
```

The file lives at `.aegis/state/aegis.log`, is plain text, and rotates at
5 MB (3 backups). Both `aegis` and `aegis serve` write to it.

Four ways an exception can escape are wired to it — an uncaught error on
the main thread, one on a worker thread, an orphaned asyncio task, and
Textual's own handler, which fires while the app is still up. Every crash
write is flushed before it returns, so the record is on disk *before* the
process gets to exit. Ordinary log lines (the scheduler firing, a plugin
failing to load) go to the same file.

A crash entry carries more than a traceback:

```
!! CRASH [tui] WorkerFailed: Worker raised exception: DuplicateIds(...)
  context: 3 tabs; live: rift-prose-checks[pane-hale-hamming], …;
           retired handles: hale-hamming, bold-backus
  -- carried by WorkerFailed --
  Traceback (most recent call last):
    …
```

`[tui]` names which of the four hooks caught it. `context:` is the tab
roster at the moment it happened — which is usually what a bare traceback
cannot tell you. And when the failure arrives wrapped (every pane mount
runs in a worker, so it comes as `WorkerFailed`, whose own traceback names
nothing), the exception it carries is unwrapped and printed with its real
frames.

## Headless mode

If you want the routing plane (sessions, queues, MCP) without the TUI,
run `aegis serve`. See [Configuration](configuration.md#headless-telegram)
for the Telegram bridge.
