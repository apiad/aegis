# aegis

aegis is the programmable meta-harness that grows with you. It runs the coding
agents you already pay for, Claude Code, OpenCode and Codex, side by side in one
browser workplace. You start with one agent in a tab. As you need more, you shape
the workplace with what is already there: agent presets and queues in one file,
agents that hand work to each other and wait on each other, and pages they write
for your decisions and dock beside the conversation. The same harness that runs
one session runs a studio: a director, team leads with their workers, QA, and you
deciding as the producer.

```bash
uv tool install aegis-harness
cd ~/your/project
aegis
```

The last line starts the server and opens it in a browser window. Python 3.13 or
newer, MIT license.

## From one agent to a studio

Climb only as far as the work needs. Each rung adds one kind of thing, and the
rungs below keep working.

1. **One agent.** A session in a browser tab under your own subscription, with a
   transcript that folds the work, the quota and its pace, and replies you send
   with one tap.
2. **Several side by side.** Sessions on different harnesses and models hand
   context to each other, read each other, and wait on CI or on each other with
   monitors instead of polling. You add agent presets.
3. **Delegated work.** Queues of workers report back as turns, plans stay where
   you see them, choices arrive as pages you click, and a long session continues
   in a fresh one at a break. You add queues.
4. **A team.** A lead spawns implementers in their own worktrees, reviews their
   pull requests and merges on green, following know-how written in the repo.
   You add briefs and procedures.
5. **A studio.** A director runs several teams on one project and brings you
   decisions as pages and checklists you mark from a phone, with the pages the
   work needs docked beside the transcript and other machines linked in. You
   are the producer.

aegis has no studio mode. Every rung uses the same tools; the structure lives in
your own files, so it is versioned, reviewed and copied to the next project like
code. The vision behind this is in
[`docs/superpowers/specs/2026-10-13-aegis-grows-with-you-vision.html`](docs/superpowers/specs/2026-10-13-aegis-grows-with-you-vision.html).

## Bring your own subscriptions

aegis calls no model. Each session is the vendor's own command-line agent,
`claude`, `opencode` or `codex`, started by aegis under the account you already
use: a Claude Pro or Max plan, OpenCode Go, OpenCode Zen, a ChatGPT plan, or any
provider OpenCode or Codex reaches. aegis holds no API key and charges nothing per token, and your prompts go
from the CLI to the vendor as they would from a terminal.

On top of the three CLIs, aegis adds what none has on its own:

- Sessions of every harness in one tab bar, each with its own model, effort and
  permission. `/model`, `/effort` and `/permission` change them on a running
  session.
- The quota of each subscription: Claude's 5-hour and weekly windows, and OpenCode
  Go's 5-hour, weekly and monthly ones. Each gauge shows how much of the window
  is spent, how much of its time has gone, and where it will land at the reset,
  from its rate so far and what your finished windows usually spend. A session's panel shows the windows that session spends.
- The Usage page shows what the agents' work cost, live: spend per week by
  purpose, repo, owner, mode or model, agent hours against time waiting on you,
  the cost per merged pull request, and each subscription's fee shared over
  what ran on it, across every linked server. `aegis usage` and the
  `usage_read` tool answer the same questions from the shell and from agents.
- Cascades: an agent can name an ordered list of models (`models:`). A session
  starts on the first one that is not cooling and whose quota is open, and a
  turn that fails on quota, a rate limit or an overload falls to the next
  model and continues. Every model pick shows the model's rank, its
  Intelligence Index from Artificial Analysis and its price, or "free".
- Each release names the harness versions it was tested with. On a version
  outside them, a session you start asks first, and every message names the
  binary found and the command that installs a tested version.

## Agents that work together

Every agent in aegis gets aegis's tools over MCP. Each session connects with its
own token, so no tool asks who is calling.

- `peer_handoff` gives work to another session. It arrives there as a new turn
  headed `> from agent:<handle>`.
- `peer_read` reads the last entries of another session's transcript, and
  `session_list` shows what every open session is doing: its mark and the line
  that goes with it, what it waits on, its plan and the step it is on, its model
  and what it has spent.
- `session_spawn` opens a new session from a configured agent, and
  `session_close` closes one. A session the agent spawned, or a worker whose task
  it enqueued, closes at once when it is done. Any other close is refused first,
  with what that session is doing and who started it, and goes through only on a
  second call with the one-time token the refusal carries. A session spawned
  with `quiet=true`, or quieted with `session_quiet`, works without a tab, as a
  dimmed card at the end of the Fleet, and gets its tab back when its turn ends.
- `queue_enqueue` hands a task to a queue: a pool of worker sessions with one
  agent and a limit on how many run at once. The result comes back as a turn in
  the session that asked, headed `> from queue:<name> · task#<id>`.
  `task_status`, `task_cancel` and `task_resume` follow it. Workers start
  quiet, so only a failed one takes a tab.
- `schedule_create` sends a prompt on a clock: every few minutes, on a cron
  line, or once at a set time, into a new session from an agent or into an open
  session's inbox. An optional bash check fires a tick only when its output
  changed, and a fire waits while the quota is over the schedule's own lines.
  The Schedules view (Alt+K) edits the same schedules and lists their runs.
- `monitor_start` watches a long process with shell commands for `done`, `fail`
  and `progress`, and wakes the agent when one of them passes or the timeout
  does. The agent ends its turn instead of polling, and the monitor sits in its
  panel with a progress bar and an ETA.
- `monitor_sessions` waits on other sessions instead of a command. It wakes the
  agent `ok` when every one of them has finished, or `blocked` as soon as one
  needs you or fails, so "when these three are done, cut the release" needs no
  polling either.
- `plan_update` and `turn_end` tell you what the agent is doing and how each turn
  ended: a question for you, something to read, or finished work, with up to
  three replies you send with one tap. A turn cannot end as finished while its
  plan still has open items.
- `toast` shows you one line mid-turn, in every open browser, as a card in the
  corner that names the session, and as a desktop notification while the page is
  hidden; a click opens its tab.
- `journal_search` asks what was done on this server, by day, words, a path or
  a session, and `journal_note` adds a decision or a blocker to it.
- `aegis call <tool> key=value ...` runs any of these tools from the agent's
  own shell, as its session, and `aegis tools` lists them. A loop over many
  calls is one Bash call, and a result piped into `wc` or `jq` never enters
  the agent's context.

The same works across machines. `aegis link add vps https://dev.example` makes
this server a client of another aegis. The far server's sessions appear in your
Fleet and your tab bar with a server tag, and they take prompts, interrupts and
closes from here. An agent here hands work to one there with
`peer_handoff(target="knuth@vps")`. The link goes one way: the far server can
never reach the one that linked it, so a shared server cannot touch your laptop.
Both servers need aegis 2.4.0 or newer; an older far server refuses the link
with an HTTP 403, and `aegis link add` says so.

Between two servers only you use, `aegis link add vps <url> --trusted` makes
the link reach both ways over the same socket, so a laptop behind NAT can be
reached from the server without a connection of its own. Agents on either side
then read, spawn, enqueue on and wait on the other's sessions as at home.

## Skills and memories every session finds

aegis keeps each person's skills and memories on the server, as Markdown files
with a git history, for every harness. Each message to a session carries a
short block naming the three skills and three memories that may matter, and the
agent loads one with `skill_read` or `memory_read`. A skill is also a
`/<name>` command in the composer. What an agent saves without being asked
waits as a card with Approve and Discard, the Knowledge view (Alt+I) edits
everything and restores earlier versions, and `/consolidate` proposes merging
duplicates and dropping what went stale. `/setup` moves in the skills and
memory files a harness already keeps.

## Work that runs as a plan

An agent can write a larger piece of work as a workflow: a tree of steps in
which every node says what it does. `workflow_arm` puts it in the transcript as
a card that reads like a plan and counts the shell commands it will run, and
nothing runs until you approve it. The server then runs each step, as a locked
session or a shell command, and the card tells the story as it goes.

- Steps run in sequence, side by side, or once per item of a list, under a pool
  of agents (`workflows: {max_agents}`) that keeps the server from filling up.
- Shell steps, checks that wait on a command or a judging agent, pauses,
  branches and loops, with deadlines and retries. A restart never runs a
  command twice.
- A failed step wakes the main agent, which retries it, skips it or changes it.
  Gates stop for the main agent or for you, and a running workflow changes only
  through a diff you approve on its card.
- The card docks, opens full screen or moves to another window, and its steps
  open read-only without adding tabs.
- `workflow_save` keeps a workflow in the project. `/workflow <id> <text>` runs
  it with no second click, `/loop` repeats a prompt until the agent says it is
  done, and a schedule can fire a saved workflow, pausing for you when it
  changes. Settings → Workflows holds the pool and the saved list.

A schedule that an agent creates also waits for you: it is born paused, with a
card that shows it whole and unpauses it.

## Agents that write their own interface

An agent that needs more than a reply in prose writes a page. `artifact_create`
gives it a working skeleton to edit, and `artifact_send` puts the page in the
transcript, live. You click, drag, pick or type, and the page answers the agent:

- a submit wakes the agent with the result and folds the page to one line;
- an event wakes the agent and leaves the page live;
- a state write waits until the agent reads it with `artifact_read`.

The agent pushes new state or a new version of the page with `artifact_update`,
and takes it back with `artifact_close`.

Before you see a page, aegis runs it in a hidden frame in your browser. A page
that throws goes back to the agent with the error, so the agent fixes it and you
only ever see a version that starts. Every page runs in its own sandboxed frame
and can use the theme's colors and type.

This is how an agent asks you to pick one of three layouts, tune a value and
confirm it, explore a chart, or answer a question with more structure than a
sentence. A page can also stay: docked beside the transcript, arranged in
columns, moved to a second browser window, or shown as tabs on a phone, so the
workplace grows with what its agents build. An agent docks a page or a file as
it sends it, and a file sent again from the same path updates its card.

## The workplace

- **What needs you.** Every tab and Fleet card carries a mark: working, needs
  you, error, review, waiting or done. The page title counts the sessions that
  need you, and Alt+J goes to the one that has waited longest. aegis knows what
  you have read in every browser.
- **Nudges.** An agent that ends a turn without saying how is asked where it
  stands two seconds later, so its card never reads done when it was not. One
  that works 15 minutes without touching an unfinished plan is reminded once
  to update it. Both arrive as rows headed `aegis:nudge`, never as your
  message.
- **Agents know what they track.** A session's next message carries a short
  status block when its plan, its monitors or its queue tasks changed, and a
  warning when the host runs short of disk, memory, CPU or quota. A monitor
  that stalls, cannot run its check or overruns its ETA wakes the agent that
  waits on it instead of leaving it asleep until the timeout.
- **The journal.** aegis records what was done from the transcripts: commits,
  pull requests, plan items done and turns that changed files. The Journal view
  (the book in the header, or Alt+L) searches it with one fuzzy box over every
  field, the side panel shows a session's own entries, and `aegis journal
  search` does the same in a shell.
- **The plan, timed.** The agent's plan shows how long each item took, the work
  and idle time so far, and an estimate of the work left. Time spent waiting on
  you never enters the estimate.
- **A transcript that reads well.** Tool calls fold to a one-line verdict, with a
  failure in red and the output and diff loaded when you open one. `z` or Alt+Z
  folds each stretch of work between two messages into one line, or everything
  but the conversation. Markdown renders, and a prompt sent mid-turn shows as
  pending until the agent reads it.
- **The session panel.** One compact row per section, with the details in a card
  that opens on hover: the session, the plan, the monitors, usage and quota, and
  the host's CPU, RAM, disk and network (whether packets leave, the exit address
  and the link's speed). A session waiting on queue workers names them. Alt+B
  collapses the panel, and you drag its edge to resize it. A Repos row lists
  each repository the session wrote to, with its uncommitted files, unpushed
  commits and open pull requests.
- **Every file on the server.** Alt+O finds any file under the config root as
  you type, and the Files view browses, edits and saves it, with a folder tree
  and a check that a session did not change it meanwhile. Select lines of a
  file, a rendered page or an artifact, or text in a transcript, to ask the
  session about them, ask aside, start a new session or collect comments;
  rows, cards, tabs and Files entries have menus on a right-click or long press.
- **Files from agents.** `file_send` hands you one file, or a set of up to
  twenty as one card that pages with ‹ ›, with a preview (images, PDF, HTML,
  Markdown, text, audio, video), Open and Download. A Read, Write or Edit row
  also has *Show the file*, which opens the file that tool used, as it is now,
  inside the row.
- **Files to agents.** Drop, paste or pick files in the message box (the
  paperclip). They upload as chips while you type, go with your message into
  the session's inbox, and the agent gets their paths. Your row shows them as
  one card. It works on a linked server's sessions too.
- **Monitors you can read.** Hovering a monitor opens its readings chart, its
  timing and each of its commands with the last exit code and stderr line, so a
  check that cannot run shows red instead of looking like one still waiting.
- **Lazy resume.** A restart brings sessions back stopped, and the next prompt
  resumes each one. Close archives a session for every browser, and the archive
  in the Fleet brings it back. With a `park:` block, a session nobody is
  watching ends its process after a while and resumes with its context on the
  next message, giving back the roughly 300 MB a Claude Code process holds.
- **On a phone.** Below 760 px the tabs get their own row, the panel becomes a
  drawer, and touch targets are 44 px. Chrome installs aegis as an app. On
  Android it builds that app through Google Play, so where Play is out of
  reach (Cuba, some corporate networks) the first install sits on
  "Installing…" and gives up; do it once over a VPN, and the installed app
  then runs without one.
- **An app that opens offline and pushes.** Installed or not, aegis keeps its
  client on the device and opens without a connection, showing what it last
  saw and queueing what you write until the server is back. The bell
  subscribes a phone to push, so a session that needs you, an error or an
  agent's toast reaches it with aegis closed.
- **Dictation.** The mic in the message box (Alt+M) transcribes in your browser
  with a 17.8 MB model downloaded once. The text lands in the box and is never
  sent on its own.
- **Keyboard and commands.** Alt chords move between the message box, the
  transcript, the Fleet and the tabs, and `?` lists every key. Ctrl+K (⌘K)
  opens a palette of every action for the view you are in. `/` opens a menu of
  aegis's commands and the agent's own commands and skills.
- **Find and copy.** Ctrl+F (⌘F) in a transcript searches every entry,
  including tool output and thinking the page has not loaded, and opens the row
  each match is in. Messages, code blocks and tool output carry a copy button
  that copies the raw text, and `c` copies the selected row.
- **Long sessions and the prompt cache.** Every turn rereads a session's whole
  context. Past 300k tokens the agent hears its size and, at a natural break,
  writes a brief; the reply row then offers "continue fresh", which starts the
  same agent with the brief and archives the old session once the new one has
  answered. A large session idle past the cache's one-hour lifetime says that
  its next message rewrites the whole context, and the Usage card counts tool
  calls, the cache hit rate and compactions.
- **Terminals.** + opens a shell, or a command from the `terminals:` block of
  `.aegis.yaml`, in a tab: direct mode for full-screen programs, a line box with
  history, and a key row on a phone. It keeps running when the browser closes,
  and a browser that opens it again replays what it printed.
- **Side questions.** `/btw` asks a running session something without stopping
  its turn. The agent that `btw:` names in the config answers from the
  transcript, in a block that folds, and the session's own agent never sees it.
- **References that say what they are.** A path in a reply shows a file or
  folder icon, or is struck out when nothing is there; `owner/repo#n` links to
  GitHub, and a URL carries its site's icon.
- **Feedback that becomes an issue.** `/feedback` in any session drafts an
  aegis issue with the agent, asking only what it still needs, and files it
  once you say yes.
- **Help that teaches.** `/tutorial` starts a guide for the five-level
  getting-started tutorial or any of four others. Every help page has a Guided
  tour button, and the Help search box asks an agent when the pages are not
  enough.
- **Ten themes.** Ink, Logbook, Electric, Phosphor, Blueprint, Amphora, Riso, Solari, Aizome and Poster.

## Configure

aegis reads `.aegis.yaml` from the nearest ancestor directory that has one:

```yaml
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  deepseek: {harness: opencode, model: opencode-go/deepseek-v4-pro, effort: high, permission: full}
  sol: {harness: codex, model: openai/gpt-6.1-sol, effort: high, permission: full}
  reviewer:
    harness: claude-code
    model: opus
    effort: max
    permission: read
    priming: |
      You review changes for correctness and report findings by severity.
queues:
  general: {agent: opus, max_parallel: 5}
recap: {agent: opus}
context: {handoff_at: 300000, cold_after: 3600, cold_min: 100000}
```

An agent is a preset. A new tab starts from one, and you can change its harness,
model, effort and permission for that session. Its `priming` is appended to
aegis's own system prompt. Nothing in agents and queues has a default: an agent names all four fields,
a queue names its agent and `max_parallel`, and one that does not is shown with
what is missing. `permission` is `read` (plan mode), `write` (accept edits),
`auto` or `full` (bypass permissions). An OpenCode or Codex agent names its
model as `provider/model`, such as `openai/gpt-6.1-sol` or
`openrouter/nvidia/nemotron-3-super-120b-a12b:free`, where the provider is one
Codex knows from its own `~/.codex/config.toml`. `recap` names the agent that writes a two-sentence recap when
you come back to a tab after a while; without it nothing is called. `context`
sets when an agent hears its context size (`handoff_at`, tokens) and when a
tab warns that its prompt cache has gone cold (`cold_after` seconds idle,
`cold_min` tokens); it is the one block with defaults, the values shown.

`aegis init` writes a first file from the harnesses installed on the machine, and
`aegis doctor` names every problem in it by its place in the file. The Settings
page (Alt+S) edits agents and queues and keeps the file's comments, and the
server follows the file as it changes, without a restart.

## Running it

`aegis` is `aegis serve --window`: once the server listens, it opens the URL in a
Chromium-family browser as an app window, without tabs or an address bar.
`--browser` or `AEGIS_BROWSER` picks the browser. Run it again while the server is
up and it only opens another window. Plain `aegis serve` opens nothing and prints
the URL, for systemd and remote hosts.

The defaults are `--port 8742 --host 127.0.0.1`. Anything but loopback has to be
asked for with `--host`. The printed token is the admin's. It lives in
`.aegis/state/token` and survives restarts; delete the file to rotate it, which
signs every browser out. `aegis token add NAME` gives another person, a linked
server or a job a token of its own, a member by default: it reads every session
and changes only the ones it starts. `aegis token list` names them and `aegis
token revoke NAME` ends one; revoking the admin's prints its replacement. Each
member spends the subscription only below its lines: in Settings, an admin gives
a token, or every member by default, a launch line and a stop line on each quota
window, or opens or closes a provider outright. Below the launch line the
member's sessions start; above it nothing new starts and its messages wait; at the
stop line aegis interrupts its turns and continues them when the gauge is back
under the launch line. The same policies sit in `.aegis/state/tokens.json`, and
`aegis token list` prints them. A member's agents run as the same Linux user as everyone's, so one with shell access
can read `.aegis/state/token` and act as the admin: tokens keep colleagues from
changing each other's sessions by accident, and give only people you trust with
the machine a token. Opening
the printed URL once signs that browser in: the server moves the token into an
HttpOnly cookie that lasts a year and drops it from the address bar, so no script
on the page can read it.

`aegis serve -d` starts the server in the background, in its own session, so it
outlives the terminal or the SSH connection. It returns once the port listens and
prints the URLs, the pid and how to stop it. The log goes to
`.aegis/state/serve.log`. For a host that must survive reboots, run plain
`aegis serve` under systemd. `aegis service install` writes the unit: it asks
for the user, the port, the root and the memory caps, proposes defaults sized
to the host, and prints the `sudo` commands that install and start it without
running them. `--yes` takes every default and `--dry-run` prints the unit
without writing it. `aegis doctor` compares an installed unit against what
`install` would write and names each setting that differs.

Every session, and everything its agent starts (test runs, browsers, builds),
lives in the server's unit, so the unit's memory settings decide what one
runaway session can take down. With `Delegate=yes`, aegis also gives each
session a cgroup of its own inside the unit, capped by `session_memory_max`
in `.aegis.yaml` (40% of the host's RAM by default), so a runaway session hits
an OOM kill inside its own cgroup and nothing else does. On a host with 8 GB,
with the user, root and paths given as flags, the unit is the one below; its
comments say why each setting is there.

```ini
[Unit]
Description=aegis on /srv/aegis
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=aegis
WorkingDirectory=/srv/aegis
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/local/bin/aegis serve --port 8742
# systemd stops the server, and the server ends its agents. A restart
# continues the turns it cut.
KillMode=mixed
Restart=always
RestartSec=3
# Every session, and everything its agent starts, lives in this unit, so these
# lines decide what one runaway session can take down.
# An OOM kill of one child is that child's problem, not a reason to stop the unit.
OOMPolicy=continue
# Throttle the unit before the host runs out, and cap it below the host's RAM,
# so a runaway hits an OOM inside the unit instead of a host-wide one.
MemoryHigh=5.5G
MemoryMax=6.5G
# In a host OOM, the kernel picks the server last. aegis raises every agent
# process it starts to oom_score_adj 500, so a runaway session dies first.
OOMScoreAdjust=-500
# Let aegis give each session a cgroup of its own below the unit's, capped by
# session_memory_max, so a runaway session is OOM-killed alone.
Delegate=yes

[Install]
WantedBy=multi-user.target
```

Leave `ManagedOOMMemoryPressure` off. Without delegation the unit is one
cgroup, and systemd-oomd would kill the server and every session together;
with it, oomd chooses among every cgroup below the unit, the server's own
`server/` included. Each session's own cap already contains a runaway. aegis
logs a warning at start when it runs under systemd and finds no delegation.

Behind a reverse proxy, name the public origin with `aegis serve --origin
https://dev.example`, keep `--host` on loopback, and let the proxy terminate TLS.
Do not proxy `/mcp`: agents reach it on loopback. Anyone with the token drives
agents that may run with full permission on that machine, so keep it secret.

## Before 2.0

Until 2.0, aegis was a terminal app: a Textual TUI with a daemon, queues,
workflows, schedules and more. That tree is kept under `legacy/` as reference and
is not installed. `pip install "aegis-harness<2"` gets its last release, and
`aegis import-legacy` copies its sessions into the archive to read.

## License

MIT
