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
`.aegis/state/token` and reused across restarts; delete the file to rotate it.
Anything but loopback must be asked for with `--host`.

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
drives agents that may run with full permission on that machine, so put a second
lock in the proxy (basic auth, or a login), and do not proxy `/mcp`: agents reach
it on loopback.

## Configuration

aegis reads two maps from `.aegis.yaml` at the root:

```yaml
default_agent: opus
agents:
  opus: {model: opus, effort: high, permission: full}
  haiku: {model: claude-haiku-4-5-20251001, effort: low, permission: read}
queues:
  general: {agent: opus, max_parallel: 5}
```

`permission` is `read` (plan mode), `write` (accept edits), `auto` or `full`
(bypass permissions). Only Claude Code profiles run today.

## What you get

- **Sessions in tabs.** The tab bar is the server's open sessions, the same in every
  browser; each browser orders them by drag. Fleet is the home view: a card per
  session and the archive below it.
- **Lazy resume.** A restart brings sessions back stopped; the next prompt resumes
  each with `claude --resume`. Stop ends a process and keeps the tab; Close archives
  the session for every browser; Reopen brings it back.
- **A transcript that reads well.** Tool rows with one-line verdicts, failures open,
  diffs for edits, rendered Markdown, a prompt sent mid-turn shown pending until
  Claude reads it, Esc to interrupt. Three themes: Ink, Logbook, Syalia.
- **Tools for agents** at `/mcp`, named `mcp__aegis__<verb>`:
  `monitor_start`, `monitor_cancel`, `monitor_list`, `queue_enqueue`,
  `task_status`, `task_cancel`, `task_resume`, `peer_handoff`, `peer_read`,
  `session_list`, `session_rename`, `meta`. Each session's `claude` connects with
  its own token, so no tool asks who is calling.

## Before 2.0

Until 2.0, aegis was a terminal app (a Textual TUI with a daemon, queues,
workflows, schedules and more). That tree is kept under `legacy/` as reference and
is not installed. `pip install "aegis-harness<2"` gets the last release of it.

## License

MIT
