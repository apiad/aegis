# aegis

A web-native workplace for coding agents. `aegis serve` runs Claude Code sessions on
your machine and serves them to browser tabs; agents in it get monitors, queues of
workers and handoffs over MCP.

## Install and run

```bash
uv tool install aegis-harness
cd ~/your/project          # the nearest ancestor with .aegis.yaml is the root
aegis                      # serves, and opens it in a browser app window
```

`aegis` is `aegis serve --window`: once the server listens it opens the URL in a
Chromium-family browser with `--app=`, a window without tabs or address bar (the
desktop default if it is Chromium-family, else the first Chrome, Chromium, Edge or
Brave found; `--browser` or `AEGIS_BROWSER` picks another). Run it again while the
server is up and it only opens another window. Plain `aegis serve` opens nothing and
prints the URL instead, for systemd and remote hosts.

`aegis serve --port 8742 --host 127.0.0.1` are the defaults. The token is kept in
`.aegis/state/token` and reused across restarts; delete the file to rotate it, which
signs every browser out. Anything but loopback must be asked for with `--host`.

Opening the printed URL once signs that browser in: the server moves the token into
an HttpOnly cookie that lasts a year and drops it from the address bar, so no script
on the page can read it. A browser without the cookie gets a field to paste the token
into.

`aegis serve -d` (`--detach`) starts the server in the background, in its own
session, so it outlives the terminal or the SSH connection without tmux or screen.
It returns once the port listens and prints the URLs, the pid and how to stop it;
the output goes to `.aegis/state/serve.log` and the pid to `.aegis/state/serve.pid`.
A server that dies while booting is reported with the end of its log, and the
command exits 1. With `--window` the window opens once it listens. For a host that
must survive reboots, run plain `aegis serve` under systemd instead.

Behind a reverse proxy, name the public origin: `aegis serve --origin
https://dev.example` (repeatable) accepts sockets whose `Host` is `dev.example` and
whose `Origin` is exactly that origin, and prints the public URL with the token.
Keep `--host` on loopback and let the proxy terminate TLS. Anyone with the token
drives agents that may run with full permission on that machine, so keep it secret,
and do not proxy `/mcp`: agents reach it on loopback. Behind https the sign-in cookie
is `Secure`.

## Configuration

aegis reads two maps from `.aegis.yaml` at the root:

```yaml
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  reviewer:
    harness: claude-code
    model: opus
    effort: max
    permission: read
    priming: |
      You review changes for correctness and report findings by severity.
queues:
  general: {agent: opus, max_parallel: 5}
```

An agent is a preset. The new tab starts from one, and any of its harness,
model, effort and permission can be changed for that session; its `priming` is
appended to aegis's own system prompt. Nothing has a default: an agent names
all four fields and a queue names its agent and `max_parallel`, and one that
does not is shown with what is missing. `permission` is `read` (plan mode),
`write` (accept edits), `auto` or `full` (bypass permissions). An agent runs Claude
Code (`harness: claude-code`) or OpenCode (`harness: opencode`, with a
`provider/model`).

`aegis init` writes a first `.aegis.yaml` from the harnesses installed on the
machine, and `aegis doctor` names every problem in one by its place in the file.
The Settings page (Alt+S) edits agents and queues and keeps the file's comments, and
the server follows the file as it changes, without a restart. `recap: {agent:
<name>}` names the agent that writes a two-sentence recap when you come back to a
tab after a while; without it nothing is called.

## What you get

- **Sessions in tabs.** The tab bar is the server's open sessions, the same in every
  browser; each browser orders them by drag. Fleet is the home view: a card per
  session and the archive below it.
- **Lazy resume.** A restart brings sessions back stopped; the next prompt resumes
  each with `claude --resume`. Stop ends a process and keeps the tab; Close archives
  the session for every browser; Reopen brings it back.
- **A transcript that reads well.** Tool rows closed on a one-line verdict, a
  failure in red, the output and the diff loaded when you open a row; rendered
  Markdown; a prompt sent mid-turn shown pending until Claude reads it; Esc or the
  ■ beside send to interrupt. Three themes: Ink, Logbook, Syalia.
- **What needs you.** Each tab and Fleet card carries a badge: working, needs you,
  error, review, waiting or done. Agents report their plan and how each turn ended,
  with up to three replies you can send with one tap. aegis knows what you have
  read on every browser, the page title counts the sessions that need you, and
  Alt+J goes to the one that has waited longest.
- **On a phone.** Below 760 px the tabs get their own row, ☰ opens the side panel
  as a drawer, and touch targets are 44 px. Chrome installs aegis from its menu as
  an app named after the server. A return to one of the last 8 tabs, or a
  reconnect after the phone slept, sends only what changed.
- **Dictation.** The mic in the message box (Alt+M) transcribes in the browser with
  Cactus Whistle; the text lands in the box and is never sent on its own. The
  first press downloads the 17.8 MB model from Hugging Face once, into
  `~/.cache/aegis/dictation/` or `AEGIS_DICTATION_DIR`. The browser needs https or
  localhost for the microphone.
- **Slash commands.** `/model`, `/effort` and `/permission` switch a running
  session; `/rename`, `/title`, `/stop` and `/close` act on it; Claude's own
  commands and skills pass through. `/` or Alt+/ opens a menu that completes
  them, and an unknown command is refused rather than sent as a prompt.
- **The keyboard.** Alt chords move between the message box, the transcript,
  Fleet and the tabs; j and k walk transcript rows and Fleet cards. `?` lists
  every key.
- **Monitors you can read.** A session's monitors sit in its sidebar with their
  progress and ETA. Hovering one opens a card with its readings chart, start,
  interval and timeout, and each of its `done`, `progress` and `fail` commands
  with the last exit code and stderr line, so a check that cannot run shows red
  instead of looking like one still waiting.
- **Files from agents.** An agent hands you a file with `file_send`; it shows in
  the transcript as a card with a preview (images, PDF, HTML, Markdown, text,
  audio, video), Open and Download. A browser on the server's own desktop also
  gets Open natively, which runs `xdg-open` (`open` on macOS, or `AEGIS_OPENER`).
  aegis keeps a copy and serves it at an unguessable `/files/` link.
- **Gauges.** The Fleet view carries session counts, CPU, RAM and disk, and your
  Claude and OpenCode Go quota windows with the share already spent and where
  the window is heading; the session sidebar shows Claude's two windows.
- **Which aegis.** The top bar and the sidebar show the running version (the
  commit, for a build from git) and the latest release on PyPI.
- **Tools for agents** at `/mcp`, named `mcp__aegis__<verb>`:
  `monitor_start`, `monitor_cancel`, `monitor_list`, `queue_enqueue`,
  `task_status`, `task_cancel`, `task_resume`, `peer_handoff`, `peer_read`,
  `session_list`, `session_spawn`, `session_rename`, `plan_update`, `turn_end`,
  `file_send`, `agents_list`, `config_doctor`, `quota_read`, `meta`. Each
  session's `claude` connects with its own token, so no tool asks who is calling.

## Before 2.0

Until 2.0, aegis was a terminal app (a Textual TUI with a daemon, queues,
workflows, schedules and more). That tree is kept under `legacy/` as reference and
is not installed. `pip install "aegis-harness<2"` gets the last release of it.

## License

MIT
