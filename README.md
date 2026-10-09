# aegis

aegis is a programmable multi-agent meta-harness. It runs the coding agents you
already pay for, Claude Code and OpenCode, side by side in one browser workplace.
Its agents hand work to each other and wait on each other, on one machine or
across several. And when prose is not enough, they write the interface they need
into the conversation.

```bash
uv tool install aegis-harness
cd ~/your/project
aegis
```

The last line starts the server and opens it in a browser window. Python 3.13 or
newer, MIT license.

## Bring your own subscriptions

aegis calls no model. Each session is the vendor's own command-line agent,
`claude` or `opencode`, started by aegis under the account you already use: a
Claude Pro or Max plan, OpenCode Go, OpenCode Zen, or any provider OpenCode
reaches. aegis holds no API key and charges nothing per token, and your prompts go
from the CLI to the vendor as they would from a terminal.

On top of the two CLIs, aegis adds what neither has on its own:

- Sessions of both harnesses in one tab bar, each with its own model, effort and
  permission. `/model`, `/effort` and `/permission` change them on a running
  session.
- The quota of each subscription: Claude's 5-hour and weekly windows, and OpenCode
  Go's 5-hour, weekly and monthly ones. Each gauge shows how much of the window
  is spent, how much of its time has gone, and where it will land at the reset
  at the current pace. A session's panel shows the windows that session spends.
- `aegis usage` reports what the work cost, by month, weekday, hour, session or
  repo, from the transcripts aegis keeps.

## Agents that work together

Every agent in aegis gets aegis's tools over MCP. Each session connects with its
own token, so no tool asks who is calling.

- `peer_handoff` gives work to another session. It arrives there as a new turn
  headed `> from agent:<handle>`.
- `peer_read` reads the last entries of another session's transcript, and
  `session_list` shows what every open session is doing: its mark and the line
  that goes with it, what it waits on, its plan and the step it is on, its model
  and what it has spent.
- `session_spawn` opens a new session from a configured agent.
- `queue_enqueue` hands a task to a queue: a pool of worker sessions with one
  agent and a limit on how many run at once. The result comes back as a turn in
  the session that asked, headed `> from queue:<name> · task#<id>`.
  `task_status`, `task_cancel` and `task_resume` follow it.
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
  three replies you send with one tap.

The same works across machines. `aegis link add vps https://dev.example` makes
this server a client of another aegis. The far server's sessions appear in your
Fleet and your tab bar with a server tag, and they take prompts, interrupts and
closes from here. An agent here hands work to one there with
`peer_handoff(target="knuth@vps")`. The link goes one way: the far server can
never reach the one that linked it, so a shared server cannot touch your laptop.

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
sentence. Today a page lives for one exchange and stays in the transcript as a
record. Next, an agent will keep the pages it writes and dock them beside the
transcript, so the workplace grows with what its agents build.

## The workplace

- **What needs you.** Every tab and Fleet card carries a mark: working, needs
  you, error, review, waiting or done. The page title counts the sessions that
  need you, and Alt+J goes to the one that has waited longest. aegis knows what
  you have read in every browser.
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
  the host's CPU, RAM and disk. Alt+B collapses the panel, and you drag its edge
  to resize it.
- **Files from agents.** `file_send` hands you one file, or a set of up to
  twenty as one card that pages with ‹ ›, with a preview (images, PDF, HTML,
  Markdown, text, audio, video), Open and Download. A Read, Write or Edit row
  also has *Show the file*, which opens the file that tool used, as it is now,
  inside the row.
- **Monitors you can read.** Hovering a monitor opens its readings chart, its
  timing and each of its commands with the last exit code and stderr line, so a
  check that cannot run shows red instead of looking like one still waiting.
- **Lazy resume.** A restart brings sessions back stopped, and the next prompt
  resumes each one. Close archives a session for every browser, and the archive
  in the Fleet brings it back.
- **On a phone.** Below 760 px the tabs get their own row, the panel becomes a
  drawer, and touch targets are 44 px. Chrome installs aegis as an app.
- **Dictation.** The mic in the message box (Alt+M) transcribes in your browser
  with a 17.8 MB model downloaded once. The text lands in the box and is never
  sent on its own.
- **Keyboard and commands.** Alt chords move between the message box, the
  transcript, the Fleet and the tabs, and `?` lists every key. `/` opens a menu
  of aegis's commands and the agent's own commands and skills.
- **Three themes.** Ink, Logbook and Syalia.

## Configure

aegis reads `.aegis.yaml` from the nearest ancestor directory that has one:

```yaml
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  deepseek: {harness: opencode, model: opencode-go/deepseek-v4-pro, effort: high, permission: full}
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
```

An agent is a preset. A new tab starts from one, and you can change its harness,
model, effort and permission for that session. Its `priming` is appended to
aegis's own system prompt. Nothing has a default: an agent names all four fields,
a queue names its agent and `max_parallel`, and one that does not is shown with
what is missing. `permission` is `read` (plan mode), `write` (accept edits),
`auto` or `full` (bypass permissions). An OpenCode agent names its model as
`provider/model`. `recap` names the agent that writes a two-sentence recap when
you come back to a tab after a while; without it nothing is called.

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
asked for with `--host`. The token lives in `.aegis/state/token` and survives
restarts; delete the file to rotate it, which signs every browser out. Opening
the printed URL once signs that browser in: the server moves the token into an
HttpOnly cookie that lasts a year and drops it from the address bar, so no script
on the page can read it.

`aegis serve -d` starts the server in the background, in its own session, so it
outlives the terminal or the SSH connection. It returns once the port listens and
prints the URLs, the pid and how to stop it. The log goes to
`.aegis/state/serve.log`. For a host that must survive reboots, run plain
`aegis serve` under systemd.

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
